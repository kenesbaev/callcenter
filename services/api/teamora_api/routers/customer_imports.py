from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from sqlalchemy import func, select

from teamora_api.audit import write_audit
from teamora_api.background_service import (
    acquire_idempotency_lock,
    append_background_job_event,
    enqueue_background_job,
)
from teamora_api.config import get_settings
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
from teamora_api.models import (
    BackgroundJob,
    Customer,
    CustomerContact,
    CustomerFieldDefinition,
    CustomerImport,
    JobCommandSubmission,
    StorageObject,
)
from teamora_api.object_storage import (
    ObjectStorageUnavailable,
    PrivateObjectStorage,
    import_object_key,
)
from teamora_api.project_access import resolve_project
from teamora_api.realtime import enqueue_realtime_event
from teamora_api.schemas.background import BackgroundImportCommitRequest, BackgroundImportRead
from teamora_api.schemas.common import Page
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
MAX_BACKGROUND_UNCOMPRESSED_XLSX_BYTES = 250 * 1024 * 1024
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


def validate_xlsx_archive(
    content: bytes, *, maximum_uncompressed_bytes: int = MAX_UNCOMPRESSED_XLSX_BYTES
) -> None:
    stream = io.BytesIO(content)
    if not zipfile.is_zipfile(stream):
        raise ApiError(422, "customer_import_xlsx_invalid", "XLSX-файл повреждён")
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        entries = archive.infolist()
        total_size = sum(info.file_size for info in entries)
        if total_size > maximum_uncompressed_bytes:
            raise ApiError(422, "customer_import_xlsx_too_large", "Распакованный XLSX слишком большой")
        if any(info.flag_bits & 0x1 for info in entries):
            raise ApiError(422, "customer_import_xlsx_encrypted", "Зашифрованные XLSX не поддерживаются")
        names = {info.filename.replace("\\", "/") for info in entries}
        if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
            raise ApiError(422, "customer_import_xlsx_invalid", "XLSX structure is incomplete")
        for info in entries:
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or ".." in Path(normalized).parts:
                raise ApiError(422, "customer_import_xlsx_unsafe_path", "Unsafe XLSX entry path")
            if info.compress_size > 0 and info.file_size / info.compress_size > 100:
                raise ApiError(422, "customer_import_xlsx_zip_bomb", "XLSX compression ratio is unsafe")
            lowered = normalized.casefold()
            if "vbaproject.bin" in lowered or lowered.startswith("xl/externallinks/"):
                raise ApiError(
                    422,
                    "customer_import_xlsx_unsafe",
                    "Macros and external links are not allowed",
                )


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
    if not isinstance(raw, dict) and {
        "headers",
        "rows",
    }.issubset(import_preview.source_rows):
        raw = import_preview.source_rows
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
    is_background_ready = import_preview.execution_mode == "background" and import_preview.status == "ready"
    if import_preview.status != "preview" and not is_background_ready:
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


def _background_status(import_record: CustomerImport, job: BackgroundJob | None) -> str:
    if import_record.status == "processing_preview":
        return "previewing"
    if import_record.status == "ready":
        return "preview_ready"
    if import_record.status == "running":
        stage = str((job.result_metadata if job else {}).get("stage", "staging"))
        return "finalizing" if stage == "finalizing" else "staging"
    return import_record.status


def _import_command_fingerprint(
    *,
    command: str,
    import_record: CustomerImport,
    actor_user_id: UUID,
    expected_version: int,
) -> str:
    mapping = ",".join(f"{key}={value}" for key, value in sorted(import_record.mapping.items()))
    value = (
        f"{command}:{import_record.id}:{import_record.project_id}:{actor_user_id}:"
        f"{expected_version}:{import_record.update_rule}:{mapping}"
    )
    return sha256(value.encode("utf-8")).hexdigest()


async def _replayed_import_command(
    session: SessionDep,
    *,
    principal: Principal,
    import_record: CustomerImport,
    command: str,
    idempotency_key: str,
    fingerprint: str,
) -> bool:
    previous = await session.scalar(
        select(JobCommandSubmission).where(
            JobCommandSubmission.tenant_id == principal.tenant_id,
            JobCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if previous is None:
        return False
    if (
        previous.command != command
        or previous.request_fingerprint != fingerprint
        or previous.response_metadata.get("import_id") != str(import_record.id)
    ):
        raise ApiError(409, "idempotency_key_reused", "Idempotency-Key was reused")
    return True


def _import_command_submission(
    *,
    principal: Principal,
    import_record: CustomerImport,
    job: BackgroundJob,
    command: str,
    idempotency_key: str,
    fingerprint: str,
) -> JobCommandSubmission:
    return JobCommandSubmission(
        tenant_id=principal.tenant_id,
        project_id=import_record.project_id,
        job_id=job.id,
        submitted_by_user_id=principal.user_id,
        command=command,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        status="completed",
        response_metadata={
            "import_id": str(import_record.id),
            "status": import_record.status,
            "job_id": str(job.id),
            "version": job.lock_version,
        },
    )


def _background_preview_data(
    import_record: CustomerImport,
) -> tuple[list[str], list[dict[str, object]]]:
    def normalize_rows(value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, object]] = []
        for index, item in enumerate(value, start=2):
            if not isinstance(item, dict):
                continue
            structured_values = item.get("values")
            values = structured_values if isinstance(structured_values, dict) else item
            raw_row_number = item.get("row_number")
            row_number = raw_row_number if isinstance(raw_row_number, int) else index
            raw_duplicates = item.get("duplicate_fields")
            raw_errors = item.get("errors")
            normalized.append(
                {
                    "row_number": row_number,
                    "values": dict(values),
                    "duplicate_fields": (
                        [str(entry) for entry in raw_duplicates] if isinstance(raw_duplicates, list) else []
                    ),
                    "errors": ([str(entry) for entry in raw_errors] if isinstance(raw_errors, list) else []),
                }
            )
        return normalized

    raw_sheet = import_record.source_rows.get(import_record.selected_sheet)
    if not isinstance(raw_sheet, dict) and {
        "headers",
        "rows",
    }.issubset(import_record.source_rows):
        raw_sheet = import_record.source_rows
    if isinstance(raw_sheet, dict):
        headers = raw_sheet.get("headers")
        rows = raw_sheet.get("rows")
        return (
            [str(value) for value in headers] if isinstance(headers, list) else [],
            normalize_rows(rows),
        )
    headers = import_record.report.get("headers")
    rows = import_record.report.get("preview_rows")
    return (
        [str(value) for value in headers] if isinstance(headers, list) else [],
        normalize_rows(rows),
    )


async def _background_import_read(session: SessionDep, import_record: CustomerImport) -> BackgroundImportRead:
    job = (
        await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.tenant_id == import_record.tenant_id,
                BackgroundJob.id == import_record.background_job_id,
            )
        )
        if import_record.background_job_id
        else None
    )
    headers, preview_rows = _background_preview_data(import_record)
    status = _background_status(import_record, job)
    started = import_record.processing_started_at
    completed = import_record.processing_completed_at
    duration_ms = (
        max(0, int((completed - started).total_seconds() * 1000))
        if started is not None and completed is not None
        else None
    )
    report_errors = import_record.report.get("errors")
    error_count = len(report_errors) if isinstance(report_errors, list) else (import_record.invalid_rows or 0)
    return BackgroundImportRead(
        id=import_record.id,
        job_id=job.id if job else None,
        project_id=import_record.project_id,
        file_name=import_record.file_name,
        file_type=import_record.file_type,
        selected_sheet=import_record.selected_sheet,
        sheet_names=import_record.sheet_names,
        headers=headers,
        mapping=import_record.mapping,
        update_rule=cast(ImportUpdateRule, import_record.update_rule),
        status=status,
        progress=import_record.progress or (job.progress if job else 0),
        preview_rows=preview_rows[: get_settings().background_import_preview_rows],
        total_rows=import_record.total_rows or import_record.row_count,
        valid_rows=import_record.valid_rows or 0,
        invalid_rows=import_record.invalid_rows or 0,
        duplicate_rows=import_record.duplicate_rows or 0,
        created=import_record.created_count or 0,
        updated=import_record.updated_count or 0,
        skipped=import_record.skipped_count or 0,
        error_count=error_count,
        processing_duration_ms=duration_ms,
        state_version=job.lock_version if job else 1,
        can_commit=status == "preview_ready" and bool(import_record.mapping),
        can_cancel=status in {"previewing", "preview_ready", "queued", "staging", "ready_to_finalize"},
        can_retry=(
            (status == "failed" or (status == "completed" and import_record.report_storage_object_id is None))
            and job is not None
            and job.status in {"failed", "dead_letter"}
            and not bool(job.result_metadata.get("retention_payload_purged"))
        ),
        report_available=import_record.report_storage_object_id is not None,
        safe_error_code=job.safe_error_code if job else None,
        created_at=import_record.created_at,
        completed_at=import_record.processing_completed_at,
    )


@router.post("/background/preview", response_model=BackgroundImportRead, status_code=202)
async def create_background_import_preview(
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    sheet_name: str | None = Form(default=None),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> BackgroundImportRead:
    project = await resolve_project(session, principal, project_id)
    settings = get_settings()
    safe_name = Path(file.filename or "import").name[:255]
    extension = Path(safe_name).suffix.casefold()
    content_type = (file.content_type or "application/octet-stream").casefold()
    if extension not in {".csv", ".xlsx"}:
        raise ApiError(415, "customer_import_type_invalid", "Only CSV and XLSX are supported")
    allowed_mimes = CSV_MIME_TYPES if extension == ".csv" else XLSX_MIME_TYPES
    if content_type not in allowed_mimes:
        raise ApiError(415, "customer_import_mime_invalid", "File MIME does not match its format")
    content = await file.read(settings.background_import_max_file_bytes + 1)
    await file.close()
    if not content:
        raise ApiError(422, "customer_import_empty", "Import file is empty")
    if len(content) > settings.background_import_max_file_bytes:
        raise ApiError(413, "customer_import_file_too_large", "Background import exceeds 25 MB")
    if extension == ".csv":
        if b"\x00" in content[:4096]:
            raise ApiError(422, "customer_import_csv_invalid", "CSV contains binary data")
    else:
        validate_xlsx_archive(
            content,
            maximum_uncompressed_bytes=MAX_BACKGROUND_UNCOMPRESSED_XLSX_BYTES,
        )
    checksum = sha256(content).hexdigest()
    await acquire_idempotency_lock(
        session,
        namespace="customer-import-preview",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    existing = await session.scalar(
        select(CustomerImport).where(
            CustomerImport.tenant_id == principal.tenant_id,
            CustomerImport.idempotency_key == idempotency_key,
            CustomerImport.execution_mode == "background",
        )
    )
    if existing is not None:
        existing_storage = await session.scalar(
            select(StorageObject).where(
                StorageObject.tenant_id == principal.tenant_id,
                StorageObject.id == existing.source_storage_object_id,
            )
        )
        same_request = (
            existing.project_id == project.id
            and existing.file_name == safe_name
            and existing.file_type == extension.removeprefix(".")
            and existing_storage is not None
            and existing_storage.checksum_sha256 == checksum
            and existing_storage.size_bytes == len(content)
            and (sheet_name is None or existing.selected_sheet == sheet_name)
        )
        if not same_request:
            raise ApiError(
                409,
                "idempotency_key_reused",
                "Idempotency-Key was reused with a different import file or project",
            )
        return await _background_import_read(session, existing)
    import_id = uuid4()
    storage_id = uuid4()
    object_key = import_object_key(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        import_id=import_id,
        kind="source",
    )
    try:
        storage = PrivateObjectStorage(settings)
        metadata = await storage.put_bytes(key=object_key, data=content, content_type=content_type)
    except ObjectStorageUnavailable as exc:
        raise ApiError(503, "storage_unavailable", str(exc)) from exc
    except Exception as exc:
        raise ApiError(503, "storage_upload_failed", "Could not store private import source") from exc
    storage_record = StorageObject(
        id=storage_id,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        bucket=settings.minio_bucket,
        object_key=object_key,
        category="import_source",
        owner_aggregate_type="customer_import",
        owner_aggregate_id=import_id,
        checksum_sha256=metadata.checksum_sha256,
        size_bytes=metadata.size_bytes,
        content_type=metadata.content_type,
        status="active",
        retention_state="retained",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        lock_version=1,
    )
    import_record = CustomerImport(
        id=import_id,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        created_by_user_id=principal.user_id,
        file_name=safe_name,
        file_type=extension.removeprefix("."),
        sheet_names=["CSV"] if extension == ".csv" else [],
        selected_sheet=sheet_name or ("CSV" if extension == ".csv" else "pending"),
        source_rows={},
        mapping={},
        update_rule="skip",
        status="processing_preview",
        row_count=0,
        report={},
        idempotency_key=idempotency_key,
        expires_at=datetime.now(UTC) + timedelta(hours=PREVIEW_TTL_HOURS),
        execution_mode="background",
        source_storage_object_id=storage_id,
        progress=0,
        total_rows=0,
        valid_rows=0,
        invalid_rows=0,
        duplicate_rows=0,
        created_count=0,
        updated_count=0,
        skipped_count=0,
    )
    session.add(storage_record)
    await session.flush()
    session.add(import_record)
    await session.flush()
    job, _ = await enqueue_background_job(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        created_by_user_id=principal.user_id,
        job_type="customer_import.prepare_preview",
        queue="imports",
        priority=60,
        safe_payload={
            "import_id": import_record.id,
            "storage_object_id": storage_record.id,
            "project_id": project.id,
        },
        idempotency_key=f"preview:{idempotency_key}",
        correlation_id=request.state.correlation_id,
    )
    import_record.background_job_id = job.id
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_import.background_preview_queued",
        resource_type="customer_import",
        resource_id=import_record.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_id": str(project.id), "file_type": import_record.file_type},
    )
    await session.flush()
    response = await _background_import_read(session, import_record)
    await session.commit()
    return response


@router.get("/background", response_model=Page[BackgroundImportRead])
async def list_background_imports(
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    project_id: UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> Page[BackgroundImportRead]:
    if project_id is not None:
        await resolve_project(session, principal, project_id, active_only=False)
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = [
        CustomerImport.tenant_id == principal.tenant_id,
        CustomerImport.execution_mode == "background",
    ]
    if project_id is not None:
        filters.append(CustomerImport.project_id == project_id)
    total = int(await session.scalar(select(func.count()).select_from(CustomerImport).where(*filters)) or 0)
    rows = list(
        await session.scalars(
            select(CustomerImport)
            .where(*filters)
            .order_by(CustomerImport.created_at.desc(), CustomerImport.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page(
        items=[await _background_import_read(session, item) for item in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/background/{import_id}/status", response_model=BackgroundImportRead)
async def get_background_import_status(
    import_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> BackgroundImportRead:
    import_record = await resolve_import(session, principal, import_id)
    if import_record.execution_mode != "background":
        raise ApiError(404, "customer_import_not_found", "Background import was not found")
    return await _background_import_read(session, import_record)


@router.post(
    "/background/{import_id}/background-commit",
    response_model=BackgroundImportRead,
    status_code=202,
)
async def commit_background_import(
    import_id: UUID,
    payload: BackgroundImportCommitRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> BackgroundImportRead:
    await acquire_idempotency_lock(
        session,
        namespace="job-command-submission",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    import_record = await resolve_import(session, principal, import_id, for_update=True)
    if import_record.execution_mode != "background":
        raise ApiError(404, "customer_import_not_found", "Background import was not found")
    fingerprint = _import_command_fingerprint(
        command="customer_import.commit",
        import_record=import_record,
        actor_user_id=principal.user_id,
        expected_version=payload.expected_version,
    )
    if await _replayed_import_command(
        session,
        principal=principal,
        import_record=import_record,
        command="customer_import.commit",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    ):
        return await _background_import_read(session, import_record)
    if import_record.expires_at <= datetime.now(UTC):
        import_record.status = "expired"
        import_record.source_rows = {}
        await session.commit()
        raise ApiError(410, "customer_import_expired", "Background import preview has expired")
    current_job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.tenant_id == principal.tenant_id,
            BackgroundJob.id == import_record.background_job_id,
        )
        .with_for_update()
    )
    if current_job and current_job.lock_version != payload.expected_version:
        raise ApiError(409, "customer_import_conflict", "Import changed; refresh and retry")
    if import_record.status in {"queued", "running", "finalizing", "completed"}:
        return await _background_import_read(session, import_record)
    if import_record.status != "ready":
        raise ApiError(409, "customer_import_preview_not_ready", "Background preview is not ready")
    if "display_name" not in import_record.mapping:
        raise ApiError(422, "customer_import_name_mapping_required", "Map the customer name column")
    import_record.mapping_snapshot = dict(import_record.mapping)
    import_record.update_policy_snapshot = import_record.update_rule
    import_record.status = "queued"
    import_record.progress = 0
    import_record.processing_started_at = None
    import_record.processing_completed_at = None
    job, _ = await enqueue_background_job(
        session,
        tenant_id=principal.tenant_id,
        project_id=import_record.project_id,
        created_by_user_id=principal.user_id,
        job_type="customer_import.process",
        queue="imports",
        priority=70,
        safe_payload={
            "import_id": import_record.id,
            "storage_object_id": import_record.source_storage_object_id,
            "project_id": import_record.project_id,
        },
        idempotency_key=f"commit:{idempotency_key}",
        correlation_id=request.state.correlation_id,
        causation_id=current_job.id if current_job else None,
    )
    session.add(
        _import_command_submission(
            principal=principal,
            import_record=import_record,
            job=job,
            command="customer_import.commit",
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
    )
    import_record.background_job_id = job.id
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_import.background_commit_queued",
        resource_type="customer_import",
        resource_id=import_record.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"update_rule": import_record.update_rule},
    )
    await session.flush()
    response = await _background_import_read(session, import_record)
    await session.commit()
    return response


@router.post("/background/{import_id}/cancel", response_model=BackgroundImportRead)
async def cancel_background_import(
    import_id: UUID,
    payload: BackgroundImportCommitRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> BackgroundImportRead:
    await acquire_idempotency_lock(
        session,
        namespace="job-command-submission",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    import_record = await resolve_import(session, principal, import_id, for_update=True)
    if import_record.execution_mode != "background":
        raise ApiError(404, "customer_import_not_found", "Background import was not found")
    fingerprint = _import_command_fingerprint(
        command="customer_import.cancel",
        import_record=import_record,
        actor_user_id=principal.user_id,
        expected_version=payload.expected_version,
    )
    if await _replayed_import_command(
        session,
        principal=principal,
        import_record=import_record,
        command="customer_import.cancel",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    ):
        return await _background_import_read(session, import_record)
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.tenant_id == principal.tenant_id,
            BackgroundJob.id == import_record.background_job_id,
        )
        .with_for_update()
    )
    if job is None:
        raise ApiError(409, "customer_import_job_missing", "Background import job is missing")
    if job.lock_version != payload.expected_version:
        raise ApiError(409, "customer_import_conflict", "Import changed; refresh and retry")
    if import_record.status in {"completed", "cancelled"}:
        return await _background_import_read(session, import_record)
    if import_record.status == "finalizing":
        raise ApiError(409, "customer_import_finalizing", "Atomic finalization cannot be interrupted")
    now = datetime.now(UTC)
    if job.status in {"running", "cancel_requested"}:
        job.status = "cancel_requested"
        import_record.status = "cancel_requested"
    else:
        if job.status not in {"completed", "failed", "dead_letter", "cancelled"}:
            job.status = "cancelled"
            job.cancelled_at = now
        import_record.status = "cancelled"
        import_record.cancelled_at = now
    job.lock_version += 1
    session.add(
        _import_command_submission(
            principal=principal,
            import_record=import_record,
            job=job,
            command="customer_import.cancel",
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_import.background_cancel_requested",
        resource_type="customer_import",
        resource_id=import_record.id,
        correlation_id=request.state.correlation_id,
    )
    await session.flush()
    response = await _background_import_read(session, import_record)
    await session.commit()
    return response


@router.post("/background/{import_id}/retry", response_model=BackgroundImportRead, status_code=202)
async def retry_background_import(
    import_id: UUID,
    payload: BackgroundImportCommitRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> BackgroundImportRead:
    await acquire_idempotency_lock(
        session,
        namespace="job-command-submission",
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
    )
    import_record = await resolve_import(session, principal, import_id, for_update=True)
    if import_record.execution_mode != "background":
        raise ApiError(404, "customer_import_not_found", "Background import was not found")
    fingerprint = _import_command_fingerprint(
        command="customer_import.retry",
        import_record=import_record,
        actor_user_id=principal.user_id,
        expected_version=payload.expected_version,
    )
    if await _replayed_import_command(
        session,
        principal=principal,
        import_record=import_record,
        command="customer_import.retry",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    ):
        return await _background_import_read(session, import_record)
    job = await session.scalar(
        select(BackgroundJob)
        .where(
            BackgroundJob.tenant_id == principal.tenant_id,
            BackgroundJob.id == import_record.background_job_id,
        )
        .with_for_update()
    )
    if job is None:
        raise ApiError(409, "customer_import_job_missing", "Background import job is missing")
    if job.lock_version != payload.expected_version:
        raise ApiError(409, "customer_import_conflict", "Import changed; refresh and retry")
    retryable_import_state = import_record.status == "failed" or (
        import_record.status == "completed" and import_record.report_storage_object_id is None
    )
    if not retryable_import_state or job.status not in {"failed", "dead_letter"}:
        raise ApiError(409, "customer_import_not_retryable", "Background import cannot be retried")
    if job.result_metadata.get("retention_payload_purged"):
        raise ApiError(
            409,
            "customer_import_payload_purged",
            "Background import payload was removed by retention and cannot be retried",
        )
    now = datetime.now(UTC)
    job.status = "pending"
    job.available_at = now
    job.started_at = None
    job.completed_at = None
    job.cancelled_at = None
    job.lease_owner = None
    job.lease_token = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.progress = 0
    job.safe_error_code = None
    job.safe_error_message = None
    if job.attempt_count >= job.max_attempts:
        job.max_attempts = job.attempt_count + 1
    job.lock_version += 1
    if import_record.status != "completed":
        import_record.status = (
            "processing_preview" if job.type == "customer_import.prepare_preview" else "queued"
        )
        import_record.progress = 0
        import_record.processing_started_at = None
        import_record.processing_completed_at = None
        import_record.cancelled_at = None
    session.add(
        _import_command_submission(
            principal=principal,
            import_record=import_record,
            job=job,
            command="customer_import.retry",
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
    )
    await append_background_job_event(
        session,
        job=job,
        event_type="manual_retry_requested",
        safe_snapshot={"status": job.status, "version": job.lock_version},
        actor_user_id=principal.user_id,
        correlation_id=request.state.correlation_id,
    )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        project_id=job.project_id,
        target_membership_id=None,
        event_type="job.progress",
        aggregate_type="background_job",
        aggregate_id=job.id,
        aggregate_version=job.lock_version,
        payload={"status": job.status, "progress": 0},
        correlation_id=request.state.correlation_id,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_import.background_retry_requested",
        resource_type="customer_import",
        resource_id=import_record.id,
        correlation_id=request.state.correlation_id,
    )
    await session.flush()
    response = await _background_import_read(session, import_record)
    await session.commit()
    return response


@router.get("/background/{import_id}/report", response_model=dict[str, str])
async def download_background_import_report(
    import_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> dict[str, str]:
    import_record = await resolve_import(session, principal, import_id)
    if import_record.execution_mode != "background" or import_record.report_storage_object_id is None:
        raise ApiError(404, "customer_import_report_not_found", "Import report is not available")
    storage_record = await session.scalar(
        select(StorageObject).where(
            StorageObject.tenant_id == principal.tenant_id,
            StorageObject.id == import_record.report_storage_object_id,
            StorageObject.status != "purged",
        )
    )
    if storage_record is None:
        raise ApiError(404, "customer_import_report_not_found", "Import report is not available")
    try:
        url = await PrivateObjectStorage(get_settings()).presigned_download(
            key=storage_record.object_key,
            filename=f"customer-import-{import_record.id}-errors.json",
        )
    except Exception as exc:
        raise ApiError(503, "storage_unavailable", "Import report is temporarily unavailable") from exc
    return {"url": url}
