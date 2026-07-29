from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "services" / "api"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class RepositorySettings:
    app_env: str
    database_url: str
    migration_database_url: str | None


def environment_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not ENV_KEY.fullmatch(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def settings_from_repository() -> RepositorySettings:
    file_values = environment_file(REPOSITORY_ROOT / ".env")

    def setting(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, file_values.get(name, default))

    database_url = setting("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required to prepare API tests")

    return RepositorySettings(
        app_env=setting("APP_ENV", "development") or "development",
        database_url=database_url,
        migration_database_url=setting("MIGRATION_DATABASE_URL"),
    )


def test_url(source: str, explicit: str | None) -> URL:
    source_url = make_url(explicit or source)
    if source_url.host not in LOCAL_HOSTS:
        raise RuntimeError("The API test runner only accepts a local PostgreSQL host")
    if explicit:
        database_name = source_url.database or ""
        if not database_name.endswith(("_test", "_pytest")):
            raise RuntimeError(
                "Explicit test database names must end in _test or _pytest"
            )
        return source_url

    source_database = source_url.database or ""
    if not SAFE_IDENTIFIER.fullmatch(source_database):
        raise RuntimeError("Cannot derive a safe test database name")
    return source_url.set(database=f"{source_database}_pytest")


def quoted_identifier(value: str) -> str:
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise RuntimeError("Unsafe PostgreSQL identifier in local test configuration")
    return f'"{value}"'


async def recreate_database(migration_url: URL, app_url: URL) -> None:
    database_name = app_url.database or ""
    app_user = app_url.username or ""
    control_engine = create_async_engine(
        migration_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with control_engine.connect() as connection:
            await connection.exec_driver_sql(
                f"DROP DATABASE IF EXISTS {quoted_identifier(database_name)} WITH (FORCE)"
            )
            await connection.exec_driver_sql(
                f"CREATE DATABASE {quoted_identifier(database_name)}"
            )
            await connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {quoted_identifier(database_name)} "
                f"TO {quoted_identifier(app_user)}"
            )
    finally:
        await control_engine.dispose()

    migration_engine = create_async_engine(migration_url)
    try:
        async with migration_engine.begin() as connection:
            await connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA public TO {quoted_identifier(app_user)}"
            )
            await connection.exec_driver_sql(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted_identifier(app_user)}"
            )
            await connection.exec_driver_sql(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT USAGE, SELECT ON SEQUENCES TO {quoted_identifier(app_user)}"
            )
    finally:
        await migration_engine.dispose()


async def drop_database(migration_url: URL, database_name: str) -> None:
    control_engine = create_async_engine(
        migration_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with control_engine.connect() as connection:
            await connection.exec_driver_sql(
                f"DROP DATABASE IF EXISTS {quoted_identifier(database_name)} WITH (FORCE)"
            )
    finally:
        await control_engine.dispose()


def rendered(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an isolated local PostgreSQL database, run API tests, and remove it."
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()

    settings = settings_from_repository()
    if settings.app_env in {"staging", "production"}:
        raise RuntimeError(
            "Refusing to derive tests from staging or production configuration"
        )

    source_app_url = str(settings.database_url)
    source_migration_url = settings.migration_database_url
    if not source_migration_url:
        raise RuntimeError(
            "MIGRATION_DATABASE_URL is required to prepare the isolated test database"
        )

    app_url = test_url(source_app_url, os.environ.get("TEST_DATABASE_URL"))
    migration_url = test_url(
        str(source_migration_url), os.environ.get("TEST_MIGRATION_DATABASE_URL")
    ).set(database=app_url.database)
    if app_url == make_url(source_app_url):
        raise RuntimeError(
            "The test database must differ from the application database"
        )

    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "ENABLE_CALL_SIMULATOR": "true",
            "DATABASE_URL": rendered(app_url),
            "MIGRATION_DATABASE_URL": rendered(migration_url),
            "TEST_DATABASE_URL": rendered(app_url),
        }
    )

    database_name = app_url.database or ""
    print(
        f"Preparing isolated PostgreSQL database {database_name!r} on localhost",
        flush=True,
    )
    asyncio.run(recreate_database(migration_url, app_url))
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(API_ROOT / "alembic.ini"),
                "upgrade",
                "head",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=True,
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(API_ROOT / "tests"),
                *arguments.pytest_args,
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
        )
        return completed.returncode
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(f"Removed isolated PostgreSQL database {database_name!r}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
