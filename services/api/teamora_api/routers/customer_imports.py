from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import cast
from uuid import UUID

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from sqlalchemy import select

from teamora_api.audit import write_audit
from teamora_api.customer_service import (
    NormalizedContact,
    contacts_for_customers,
    duplicate_customer_fields,
    field_definitions_for_project,
    normalize_contacts,
    replace_customer_contacts,
    validate_custom_field_value,
)
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import LanguageCode
from teamora_api.errors import ApiError
from teamora_api.models import Customer, CustomerContact, CustomerFieldDefinition, CustomerImport
from teamora_api.project_access import resolve_project
from teamora_api.schemas.crm import (
    ContactKind,
    CustomerContactInput,
    CustomerImportError,
    CustomerImportMappingUpdate,
    CustomerImportPreviewRead,
    CustomerImportReport,
    CustomerImportRow,
    ImportUpdateRule,
    normalize_external_reference,
    normalized_tags,
)

router = APIRouter(prefix="/customers/import", tags=["customer-imports"])

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_UNCOMPRESSED_XLSX_BYTES = 20 * 1024 * 1024
MAX_IMPORT_ROWS = 500
MAX_IMPORT_COLUMNS = 100
MAX_IMPORT_SHEETS = 10
PREVIEW_ROW_LIMIT = 50
PREVIEW_TTL_HOURS = 24

CSV_MIME_TYPES = {
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
    "text/plain",
    "application/octet-stream",
}
XLSX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
}

STANDARD_IMPORT_FIELDS = {
    "display_name",
    "phone",
    "alternate_phone",
    "email",
    "external_reference",
    "preferred_language",
    "status",
    "city",
    "region",
    "address",
    "job_title",
    "organization",
    "tags",
    "description",
    "source",
    "next_contact_at",
}

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "display_name": ("фио", "имя", "клиент", "display name", "name", "full name"),
    "phone": ("телефон", "телефон 1", "phone", "mobile", "номер"),
    "alternate_phone": ("телефон 2", "дополнительный телефон", "alternate phone"),
    "email": ("email", "e-mail", "почта", "электронная почта"),
    "external_reference": ("external id", "external_reference", "внешний id", "id клиента"),
    "preferred_language": ("язык", "language", "preferred language"),
    "status": ("статус", "status"),
    "city": ("город", "city"),
    "region": ("регион", "область", "region"),
    "address": ("адрес", "address"),
    "job_title": ("должность", "job title", "position"),
    "organization": ("организация", "компания", "organization", "company"),
    "tags": ("теги", "tags"),
    "description": ("описание", "description"),
    "source": ("источник", "source"),
    "next_contact_at": ("следующий контакт", "next contact", "next_contact_at"),
}


def normalize_header(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").split())


def cell_to_json(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def validate_headers(raw_headers: list[object]) -> list[str]:
    headers = [str(value).strip() if value is not None else "" for value in raw_headers]
    while headers and not headers[-1]:
        headers.pop()
    if not headers or any(not header for header in headers):
        raise ApiError(422, "customer_import_headers_invalid", "Все колонки должны иметь заголовок")
    if len(headers) > MAX_IMPORT_COLUMNS:
        raise ApiError(422, "customer_import_too_many_columns", "Слишком много колонок в файле")
    normalized = [normalize_header(header) for header in headers]
    if len(set(normalized)) != len(normalized):
        raise ApiError(422, "customer_import_headers_duplicate", "Заголовки колонок повторяются")
    return headers


def read_csv(content: bytes) -> dict[str, object]:
    decoded: str | None = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ApiError(422, "customer_import_encoding_invalid", "CSV должен быть в UTF-8 или Windows-1251")
    try:
        dialect = csv.Sniffer().sniff(decoded[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(decoded), dialect)
    rows = list(reader)
    if not rows:
        raise ApiError(422, "customer_import_empty", "Файл не содержит строк")
    headers = validate_headers(list(rows[0]))
    data_rows: list[dict[str, object]] = []
    for row in rows[1:]:
        padded = list(row[: len(headers)]) + [""] * max(0, len(headers) - len(row))
        if not any(str(value).strip() for value in padded):
            continue
        data_rows.append({header: cell_to_json(value) for header, value in zip(headers, padded, strict=True)})
    if len(data_rows) > MAX_IMPORT_ROWS:
        raise ApiError(
            422,
            "customer_import_too_many_rows",
            f"Синхронный импорт ограничен {MAX_IMPORT_ROWS} строками",
        )
    return {"headers": headers, "rows": data_rows}


def validate_xlsx_archive(content: bytes) -> None:
    stream = io.BytesIO(content)
    if not zipfile.is_zipfile(stream):
        raise ApiError(422, "customer_import_xlsx_invalid", "XLSX-файл повреждён")
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        total_size = sum(info.file_size for info in archive.infolist())
        if total_size > MAX_UNCOMPRESSED_XLSX_BYTES:
            raise ApiError(422, "customer_import_xlsx_too_large", "Распакованный XLSX слишком большой")
        if any(info.flag_bits & 0x1 for info in archive.infolist()):
            raise ApiError(422, "customer_import_xlsx_encrypted", "Зашифрованные XLSX не поддерживаются")


def read_xlsx(content: bytes) -> dict[str, object]:
    validate_xlsx_archive(content)
    load_workbook = import_module("openpyxl").load_workbook
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ApiError(422, "customer_import_xlsx_invalid", "Не удалось прочитать XLSX") from exc
    if len(workbook.sheetnames) > MAX_IMPORT_SHEETS:
        workbook.close()
        raise ApiError(422, "customer_import_too_many_sheets", "В XLSX слишком много листов")
    sheets: dict[str, object] = {}
    try:
        for sheet_name in workbook.sheetnames:
            worksheet = workbook[sheet_name]
            iterator = worksheet.iter_rows(values_only=True)
            first_row = next(iterator, None)
            if first_row is None:
                continue
            headers = validate_headers(list(first_row))
            data_rows: list[dict[str, object]] = []
            for raw_row in iterator:
                values = list(raw_row[: len(headers)]) + [""] * max(0, len(headers) - len(raw_row))
                if not any(str(value).strip() for value in values if value is not None):
                    continue
                data_rows.append(
                    {header: cell_to_json(value) for header, value in zip(headers, values, strict=True)}
                )
                if len(data_rows) > MAX_IMPORT_ROWS:
                    raise ApiError(
                        422,
                        "customer_import_too_many_rows",
                        f"Лист XLSX ограничен {MAX_IMPORT_ROWS} строками",
                    )
            sheets[sheet_name] = {"headers": headers, "rows": data_rows}
    finally:
        workbook.close()
    if not sheets:
        raise ApiError(422, "customer_import_empty", "XLSX не содержит непустых листов")
    return sheets


def suggest_mapping(headers: list[str], definitions: list[CustomerFieldDefinition]) -> dict[str, str]:
    normalized_headers = {normalize_header(header): header for header in headers}
    mapping: dict[str, str] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            header = normalized_headers.get(normalize_header(alias))
            if header is not None:
                mapping[field] = header
                break
    for definition in definitions:
        candidates = (definition.key, definition.name, f"custom.{definition.key}")
        for candidate in candidates:
            header = normalized_headers.get(normalize_header(candidate))
            if header is not None:
                mapping[f"custom.{definition.key}"] = header
                break
    return mapping


def validate_mapping(
    mapping: dict[str, str],
    headers: list[str],
    definitions: list[CustomerFieldDefinition],
) -> dict[str, str]:
    allowed = STANDARD_IMPORT_FIELDS | {f"custom.{definition.key}" for definition in definitions}
    unknown_fields = sorted(set(mapping) - allowed)
    if unknown_fields:
        raise ApiError(
            422,
            "customer_import_mapping_field_invalid",
            f"Неизвестные поля mapping: {', '.join(unknown_fields)}",
        )
    missing_headers = sorted({header for header in mapping.values() if header not in headers})
    if missing_headers:
        raise ApiError(
            422,
            "customer_import_mapping_header_invalid",
            f"Колонки отсутствуют в выбранном листе: {', '.join(missing_headers)}",
        )
    used_headers = [header for header in mapping.values() if header]
    if len(used_headers) != len(set(used_headers)):
        raise ApiError(422, "customer_import_mapping_duplicate", "Одна колонка назначена нескольким полям")
    if "display_name" not in mapping:
        raise ApiError(422, "customer_import_name_mapping_required", "Назначьте колонку ФИО")
    return {field: header for field, header in mapping.items() if header}


def string_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_boolean(value: str) -> bool:
    normalized = value.casefold()
    if normalized in {"1", "true", "yes", "да", "y"}:
        return True
    if normalized in {"0", "false", "no", "нет", "n"}:
        return False
    raise ValueError("ожидается да/нет или true/false")


def import_custom_value(definition: CustomerFieldDefinition, raw: object) -> object:
    value = string_value(raw)
    if not value:
        return None
    if definition.field_type == "number":
        parsed: object = float(value.replace(",", "."))
    elif definition.field_type == "boolean":
        parsed = parse_boolean(value)
    elif definition.field_type == "multiselect":
        parsed = [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    else:
        parsed = value
    return validate_custom_field_value(definition, parsed)


def mapped_customer_values(
    raw_row: dict[str, object],
    mapping: dict[str, str],
    definitions: list[CustomerFieldDefinition],
) -> tuple[dict[str, object], list[CustomerContactInput], list[str]]:
    values: dict[str, object] = {}
    contacts: list[CustomerContactInput] = []
    errors: list[str] = []
    definition_by_key = {definition.key: definition for definition in definitions}
    for field, header in mapping.items():
        raw = raw_row.get(header, "")
        value = string_value(raw)
        if field == "phone" and value:
            contacts.append(CustomerContactInput(kind="phone", value=value, is_primary=True))
        elif field == "alternate_phone" and value:
            contacts.append(CustomerContactInput(kind="phone", value=value))
        elif field == "email" and value:
            contacts.append(CustomerContactInput(kind="email", value=value, is_primary=True))
        elif field == "tags" and value:
            values[field] = normalized_tags([item for item in value.replace(";", ",").split(",")])
        elif field == "preferred_language" and value:
            language = value.casefold()
            aliases = {"русский": "ru", "узбекский": "uz", "english": "en", "қарақалпақ": "kaa"}
            language = aliases.get(language, language)
            values[field] = LanguageCode(language).value
        elif field == "next_contact_at" and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Дата следующего контакта должна содержать часовой пояс")
            values[field] = parsed.isoformat()
        elif field.startswith("custom."):
            key = field.removeprefix("custom.")
            definition = definition_by_key[key]
            try:
                custom_value = import_custom_value(definition, raw)
                if custom_value is not None:
                    cast(dict[str, object], values.setdefault("custom_fields", {}))[key] = custom_value
            except (TypeError, ValueError) as exc:
                errors.append(f"{definition.name}: {exc}")
        elif value:
            values[field] = value
    name = string_value(values.get("display_name", ""))
    if len(name) < 2:
        errors.append("ФИО обязательно и должно содержать минимум 2 символа")
    if "status" in values and values["status"] not in {
        "new",
        "assigned",
        "callback",
        "completed",
        "do_not_call",
    }:
        errors.append("Некорректный статус клиента")
    custom_fields = cast(dict[str, object], values.get("custom_fields", {}))
    for definition in definitions:
        if (
            definition.is_active
            and definition.is_required
            and definition.key not in custom_fields
            and definition.default_value is None
        ):
            errors.append(f"Обязательное поле «{definition.name}» не заполнено")
        if (
            definition.is_active
            and definition.key not in custom_fields
            and definition.default_value is not None
        ):
            custom_fields[definition.key] = definition.default_value
    if custom_fields:
        values["custom_fields"] = custom_fields
    return values, contacts, errors


def selected_sheet_data(import_preview: CustomerImport) -> tuple[list[str], list[dict[str, object]]]:
    raw = import_preview.source_rows.get(import_preview.selected_sheet)
    if not isinstance(raw, dict):
        raise ApiError(409, "customer_import_sheet_missing", "Выбранный лист больше недоступен")
    headers = raw.get("headers")
    rows = raw.get("rows")
    if not isinstance(headers, list) or not isinstance(rows, list):
        raise ApiError(409, "customer_import_data_invalid", "Данные preview повреждены")
    return [str(header) for header in headers], [cast(dict[str, object], row) for row in rows]


async def build_preview(
    session: SessionDep,
    import_preview: CustomerImport,
) -> CustomerImportPreviewRead:
    headers, source_rows = selected_sheet_data(import_preview)
    definitions = await field_definitions_for_project(
        session,
        import_preview.tenant_id,
        import_preview.project_id,
        include_inactive=False,
    )
    mapping = validate_mapping(import_preview.mapping, headers, definitions)
    preview_rows: list[CustomerImportRow] = []
    file_identities: set[tuple[str, str]] = set()
    valid_rows = 0
    duplicate_rows = 0
    error_rows = 0
    for index, raw_row in enumerate(source_rows, start=2):
        row_errors: list[str] = []
        try:
            values, contact_inputs, mapping_errors = mapped_customer_values(raw_row, mapping, definitions)
            row_errors.extend(mapping_errors)
            contacts = normalize_contacts(contact_inputs)
        except (ApiError, TypeError, ValueError) as exc:
            values = {}
            contacts = []
            row_errors.append(exc.message if isinstance(exc, ApiError) else str(exc))
        duplicate_fields: set[str] = set()
        external_reference = string_value(values.get("external_reference", "")) or None
        database_matches = await duplicate_customer_fields(
            session,
            tenant_id=import_preview.tenant_id,
            project_id=import_preview.project_id,
            contacts=contacts,
            external_reference=external_reference,
        )
        for fields in database_matches.values():
            duplicate_fields.update(fields)
        identities: list[tuple[str, str]] = [(contact.kind, contact.normalized_value) for contact in contacts]
        _, external_normalized = normalize_external_reference(external_reference)
        if external_normalized:
            identities.append(("external_reference", external_normalized))
        for identity in identities:
            if identity in file_identities:
                duplicate_fields.add(identity[0])
            file_identities.add(identity)
        if row_errors:
            error_rows += 1
        elif duplicate_fields:
            duplicate_rows += 1
        else:
            valid_rows += 1
        if len(preview_rows) < PREVIEW_ROW_LIMIT:
            preview_rows.append(
                CustomerImportRow(
                    row_number=index,
                    values=values,
                    duplicate_fields=sorted(duplicate_fields),
                    errors=row_errors,
                )
            )
    return CustomerImportPreviewRead(
        id=import_preview.id,
        project_id=import_preview.project_id,
        file_name=import_preview.file_name,
        file_type=import_preview.file_type,
        sheet_names=import_preview.sheet_names,
        selected_sheet=import_preview.selected_sheet,
        headers=headers,
        mapping=mapping,
        update_rule=cast(ImportUpdateRule, import_preview.update_rule),
        total_rows=len(source_rows),
        valid_rows=valid_rows,
        duplicate_rows=duplicate_rows,
        error_rows=error_rows,
        rows=preview_rows,
        expires_at=import_preview.expires_at,
    )


async def resolve_import(
    session: SessionDep,
    principal: Principal,
    import_id: UUID,
    *,
    for_update: bool = False,
) -> CustomerImport:
    statement = select(CustomerImport).where(
        CustomerImport.tenant_id == principal.tenant_id,
        CustomerImport.id == import_id,
    )
    if for_update:
        statement = statement.with_for_update(of=CustomerImport)
    import_preview = await session.scalar(statement)
    if import_preview is None:
        raise ApiError(404, "customer_import_not_found", "Preview импорта не найден")
    await resolve_project(session, principal, import_preview.project_id, active_only=False)
    return import_preview


@router.post("/preview", response_model=CustomerImportPreviewRead, status_code=201)
async def create_import_preview(
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    sheet_name: str | None = Form(default=None),
) -> CustomerImportPreviewRead:
    project = await resolve_project(session, principal, project_id)
    safe_name = Path(file.filename or "import").name[:255]
    extension = Path(safe_name).suffix.casefold()
    content_type = (file.content_type or "").casefold()
    if extension not in {".csv", ".xlsx"}:
        raise ApiError(415, "customer_import_type_invalid", "Поддерживаются только CSV и XLSX")
    allowed_mimes = CSV_MIME_TYPES if extension == ".csv" else XLSX_MIME_TYPES
    if content_type not in allowed_mimes:
        raise ApiError(415, "customer_import_mime_invalid", "MIME-тип файла не соответствует формату")
    content = await file.read(MAX_IMPORT_BYTES + 1)
    await file.close()
    if not content:
        raise ApiError(422, "customer_import_empty", "Файл пуст")
    if len(content) > MAX_IMPORT_BYTES:
        raise ApiError(413, "customer_import_file_too_large", "Файл превышает лимит 2 МБ")
    source_sheets = {"CSV": read_csv(content)} if extension == ".csv" else read_xlsx(content)
    sheet_names = list(source_sheets)
    selected_sheet = sheet_name or sheet_names[0]
    if selected_sheet not in source_sheets:
        raise ApiError(422, "customer_import_sheet_invalid", "Указанный лист отсутствует в XLSX")
    definitions = await field_definitions_for_project(
        session,
        principal.tenant_id,
        project.id,
        include_inactive=False,
    )
    selected = cast(dict[str, object], source_sheets[selected_sheet])
    headers = cast(list[str], selected["headers"])
    mapping = suggest_mapping(headers, definitions)
    if "display_name" not in mapping:
        mapping["display_name"] = headers[0]
    import_preview = CustomerImport(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        created_by_user_id=principal.user_id,
        file_name=safe_name,
        file_type=extension.removeprefix("."),
        sheet_names=sheet_names,
        selected_sheet=selected_sheet,
        source_rows=source_sheets,
        mapping=mapping,
        update_rule="skip",
        status="preview",
        row_count=len(cast(list[object], selected["rows"])),
        report={},
        expires_at=datetime.now(UTC) + timedelta(hours=PREVIEW_TTL_HOURS),
    )
    session.add(import_preview)
    await session.flush()
    response = await build_preview(session, import_preview)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_import.preview_created",
        resource_type="customer_import",
        resource_id=import_preview.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={
            "project_id": str(project.id),
            "file_type": import_preview.file_type,
            "row_count": response.total_rows,
        },
    )
    await session.commit()
    return response


@router.patch("/{import_id}", response_model=CustomerImportPreviewRead)
async def update_import_mapping(
    import_id: UUID,
    payload: CustomerImportMappingUpdate,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerImportPreviewRead:
    import_preview = await resolve_import(session, principal, import_id, for_update=True)
    if import_preview.status != "preview":
        raise ApiError(409, "customer_import_already_committed", "Импорт уже завершён")
    if import_preview.expires_at <= datetime.now(UTC):
        import_preview.status = "expired"
        await session.commit()
        raise ApiError(410, "customer_import_expired", "Preview импорта истёк")
    selected_sheet = payload.sheet_name or import_preview.selected_sheet
    if selected_sheet not in import_preview.sheet_names:
        raise ApiError(422, "customer_import_sheet_invalid", "Указанный лист отсутствует")
    import_preview.selected_sheet = selected_sheet
    import_preview.mapping = payload.mapping
    import_preview.update_rule = payload.update_rule
    _, rows = selected_sheet_data(import_preview)
    import_preview.row_count = len(rows)
    response = await build_preview(session, import_preview)
    await session.commit()
    return response


def merged_contacts(
    existing: list[CustomerContact],
    incoming: list[NormalizedContact],
) -> list[CustomerContactInput]:
    by_identity = {(contact.kind, contact.normalized_value): contact for contact in existing}
    incoming_primary_kinds = {contact.kind for contact in incoming if contact.is_primary}
    result = [
        CustomerContactInput(
            id=contact.id,
            kind=cast(ContactKind, contact.kind),
            value=contact.display_value,
            label=contact.label,
            is_primary=contact.is_primary and contact.kind not in incoming_primary_kinds,
        )
        for contact in existing
    ]
    for contact in incoming:
        current = by_identity.get((contact.kind, contact.normalized_value))
        if current is not None:
            for index, value in enumerate(result):
                if value.id == current.id:
                    result[index] = CustomerContactInput(
                        id=current.id,
                        kind=contact.kind,
                        value=contact.display_value,
                        label=contact.label or current.label,
                        is_primary=contact.is_primary or current.is_primary,
                    )
                    break
        else:
            result.append(
                CustomerContactInput(
                    kind=contact.kind,
                    value=contact.display_value,
                    label=contact.label,
                    is_primary=contact.is_primary,
                )
            )
    return result


async def create_customer_from_import(
    session: SessionDep,
    import_preview: CustomerImport,
    values: dict[str, object],
    contacts: list[NormalizedContact],
) -> Customer:
    external_reference, external_normalized = normalize_external_reference(
        string_value(values.get("external_reference", "")) or None
    )
    language = LanguageCode(string_value(values.get("preferred_language", "ru")) or "ru")
    next_contact_raw = values.get("next_contact_at")
    next_contact = datetime.fromisoformat(str(next_contact_raw)) if next_contact_raw else None
    customer = Customer(
        tenant_id=import_preview.tenant_id,
        project_id=import_preview.project_id,
        display_name=string_value(values.get("display_name", "")),
        external_reference=external_reference,
        external_reference_normalized=external_normalized,
        preferred_language=language,
        status=string_value(values.get("status", "new")) or "new",
        city=string_value(values.get("city", "")) or None,
        region=string_value(values.get("region", "")) or None,
        address=string_value(values.get("address", "")) or None,
        job_title=string_value(values.get("job_title", "")) or None,
        organization=string_value(values.get("organization", "")) or None,
        tags=cast(list[str], values.get("tags", [])),
        description=string_value(values.get("description", "")),
        source=string_value(values.get("source", "")) or None,
        next_call_at=next_contact,
        custom_fields=cast(dict[str, object], values.get("custom_fields", {})),
    )
    session.add(customer)
    await session.flush()
    await replace_customer_contacts(session, customer=customer, contacts=contacts)
    return customer


async def update_customer_from_import(
    session: SessionDep,
    customer: Customer,
    values: dict[str, object],
    incoming_contacts: list[NormalizedContact],
) -> None:
    scalar_fields = {
        "display_name": "display_name",
        "city": "city",
        "region": "region",
        "address": "address",
        "job_title": "job_title",
        "organization": "organization",
        "description": "description",
        "source": "source",
        "status": "status",
    }
    for source_field, model_field in scalar_fields.items():
        if source_field in values:
            setattr(customer, model_field, string_value(values[source_field]) or None)
    if "tags" in values:
        customer.tags = cast(list[str], values["tags"])
    if "preferred_language" in values:
        customer.preferred_language = LanguageCode(string_value(values["preferred_language"]))
    if "next_contact_at" in values:
        customer.next_call_at = datetime.fromisoformat(string_value(values["next_contact_at"]))
    if "external_reference" in values:
        customer.external_reference, customer.external_reference_normalized = normalize_external_reference(
            string_value(values["external_reference"]) or None
        )
    if "custom_fields" in values:
        customer.custom_fields = {
            **customer.custom_fields,
            **cast(dict[str, object], values["custom_fields"]),
        }
    if incoming_contacts:
        grouped = await contacts_for_customers(session, customer.tenant_id, [customer.id])
        merged = merged_contacts(grouped[customer.id], incoming_contacts)
        await replace_customer_contacts(
            session,
            customer=customer,
            contacts=normalize_contacts(merged),
        )


@router.post("/{import_id}/commit", response_model=CustomerImportReport)
async def commit_customer_import(
    import_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> CustomerImportReport:
    import_preview = await resolve_import(session, principal, import_id, for_update=True)
    if import_preview.status == "committed":
        return CustomerImportReport.model_validate(import_preview.report)
    if import_preview.expires_at <= datetime.now(UTC):
        import_preview.status = "expired"
        await session.commit()
        raise ApiError(410, "customer_import_expired", "Preview импорта истёк")
    reused = await session.scalar(
        select(CustomerImport.id).where(
            CustomerImport.tenant_id == principal.tenant_id,
            CustomerImport.idempotency_key == idempotency_key,
            CustomerImport.id != import_preview.id,
        )
    )
    if reused is not None:
        raise ApiError(409, "idempotency_key_reused", "Idempotency-Key уже использован")
    import_preview.idempotency_key = idempotency_key
    headers, source_rows = selected_sheet_data(import_preview)
    definitions = await field_definitions_for_project(
        session,
        principal.tenant_id,
        import_preview.project_id,
        include_inactive=False,
    )
    mapping = validate_mapping(import_preview.mapping, headers, definitions)
    created = 0
    updated = 0
    skipped = 0
    duplicates = 0
    errors: list[CustomerImportError] = []
    file_identities: set[tuple[str, str]] = set()
    for row_number, raw_row in enumerate(source_rows, start=2):
        try:
            values, contact_inputs, row_errors = mapped_customer_values(raw_row, mapping, definitions)
            contacts = normalize_contacts(contact_inputs)
        except (ApiError, TypeError, ValueError) as exc:
            values = {}
            contacts = []
            row_errors = [exc.message if isinstance(exc, ApiError) else str(exc)]
        identities: list[tuple[str, str]] = [(contact.kind, contact.normalized_value) for contact in contacts]
        external_reference = string_value(values.get("external_reference", "")) or None
        _, external_normalized = normalize_external_reference(external_reference)
        if external_normalized:
            identities.append(("external_reference", external_normalized))
        if any(identity in file_identities for identity in identities):
            row_errors.append("Строка дублирует контакт или внешний ID внутри файла")
        file_identities.update(identities)
        if row_errors:
            skipped += 1
            errors.append(CustomerImportError(row_number=row_number, messages=row_errors))
            continue
        matches = await duplicate_customer_fields(
            session,
            tenant_id=principal.tenant_id,
            project_id=import_preview.project_id,
            contacts=contacts,
            external_reference=external_reference,
        )
        if matches:
            duplicates += 1
            if import_preview.update_rule == "skip":
                skipped += 1
                continue
            if len(matches) != 1:
                skipped += 1
                errors.append(
                    CustomerImportError(
                        row_number=row_number,
                        messages=["Контакты строки относятся к разным существующим клиентам"],
                    )
                )
                continue
            customer_id = next(iter(matches))
            customer = await session.scalar(
                select(Customer)
                .where(
                    Customer.tenant_id == principal.tenant_id,
                    Customer.project_id == import_preview.project_id,
                    Customer.id == customer_id,
                )
                .with_for_update()
            )
            if customer is None:
                skipped += 1
                errors.append(
                    CustomerImportError(
                        row_number=row_number,
                        messages=["Найденный клиент больше недоступен"],
                    )
                )
                continue
            await update_customer_from_import(session, customer, values, contacts)
            updated += 1
        else:
            await create_customer_from_import(session, import_preview, values, contacts)
            created += 1
    committed_at = datetime.now(UTC)
    report = CustomerImportReport(
        import_id=import_preview.id,
        created=created,
        updated=updated,
        skipped=skipped,
        duplicates=duplicates,
        errors=errors,
        committed_at=committed_at,
    )
    import_preview.status = "committed"
    import_preview.committed_at = committed_at
    import_preview.report = report.model_dump(mode="json")
    import_preview.source_rows = {}
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_import.committed",
        resource_type="customer_import",
        resource_id=import_preview.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={
            "project_id": str(import_preview.project_id),
            "created": created,
            "updated": updated,
            "skipped": skipped,
        },
    )
    await session.commit()
    return report
