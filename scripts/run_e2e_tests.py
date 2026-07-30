from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from run_api_tests import (
    API_ROOT,
    REPOSITORY_ROOT,
    drop_database,
    recreate_database,
    rendered,
    settings_from_repository,
    test_url,
)


def node_executable() -> str:
    configured = os.environ.get("NODE_BINARY")
    discovered = shutil.which(configured or "node")
    if discovered:
        return discovered

    codex_runtime = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "bin"
        / "node.exe"
    )
    if codex_runtime.is_file():
        return str(codex_runtime)
    raise RuntimeError("Node.js 22+ was not found; set NODE_BINARY to its executable")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Playwright against an isolated local PostgreSQL database."
    )
    parser.add_argument("playwright_args", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    settings = settings_from_repository()
    if settings.app_env in {"staging", "production"}:
        raise RuntimeError(
            "Refusing to derive E2E tests from staging or production configuration"
        )
    if not settings.migration_database_url:
        raise RuntimeError(
            "MIGRATION_DATABASE_URL is required to prepare the isolated E2E database"
        )

    app_url = test_url(settings.database_url, os.environ.get("E2E_DATABASE_URL"))
    migration_url = test_url(
        settings.migration_database_url,
        os.environ.get("E2E_MIGRATION_DATABASE_URL"),
    ).set(database=app_url.database)
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "ENABLE_CALL_SIMULATOR": "true",
            "RATE_LIMIT_REQUESTS_PER_MINUTE": "10000",
            "DATABASE_URL": rendered(app_url),
            "MIGRATION_DATABASE_URL": rendered(migration_url),
            "E2E_DATABASE_URL": rendered(app_url),
            "E2E_MIGRATION_DATABASE_URL": rendered(migration_url),
            "PYTHON_BINARY": sys.executable,
        }
    )

    database_name = app_url.database or ""
    print(
        f"Preparing isolated PostgreSQL database {database_name!r} for E2E tests",
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
                node_executable(),
                str(
                    REPOSITORY_ROOT / "node_modules" / "@playwright" / "test" / "cli.js"
                ),
                "test",
                *arguments.playwright_args,
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
        )
        return completed.returncode
    finally:
        asyncio.run(drop_database(migration_url, database_name))
        print(f"Removed isolated E2E database {database_name!r}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
