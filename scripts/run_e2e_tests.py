from __future__ import annotations

import argparse
import asyncio
import ctypes
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from run_api_tests import (
    API_ROOT,
    REPOSITORY_ROOT,
    drop_database,
    recreate_database,
    rendered,
    settings_from_repository,
    test_url,
)

DEFAULT_RUN_TIMEOUT_SECONDS = 240
DEFAULT_SERVER_TIMEOUT_SECONDS = 120
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 10


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


def bounded_seconds(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class ProcessEntry:
    pid: int
    parent_pid: int
    executable: str


def windows_processes() -> dict[int, ProcessEntry]:
    if os.name != "nt":
        return {}

    max_path = 260

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * max_path),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return {}
    entry = ProcessEntry32()
    entry.dwSize = ctypes.sizeof(entry)
    processes: dict[int, ProcessEntry] = {}
    try:
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            pid = int(entry.th32ProcessID)
            processes[pid] = ProcessEntry(
                pid=pid,
                parent_pid=int(entry.th32ParentProcessID),
                executable=entry.szExeFile,
            )
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return processes


def process_descendants(root_pids: set[int]) -> list[ProcessEntry]:
    if os.name != "nt":
        return []
    processes = windows_processes()
    descendants = set(root_pids)
    changed = True
    while changed:
        changed = False
        for process in processes.values():
            if process.parent_pid in descendants and process.pid not in descendants:
                descendants.add(process.pid)
                changed = True
    return [processes[pid] for pid in sorted(descendants) if pid in processes]


class WindowsJob:
    def __init__(self) -> None:
        self._handle: int | None = None
        if os.name != "nt":
            return

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        configured = kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        if not configured:
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = int(handle)

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self._handle is None or process.poll() is not None:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        assigned = kernel32.AssignProcessToJobObject(
            wintypes.HANDLE(self._handle),
            wintypes.HANDLE(int(process._handle)),  # type: ignore[attr-defined]
        )
        if not assigned and process.poll() is None:
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle is None:
            return
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
            wintypes.HANDLE(self._handle)
        )
        self._handle = None


class ProcessSupervisor:
    def __init__(self, shutdown_timeout_seconds: int) -> None:
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.processes: list[tuple[str, subprocess.Popen[bytes]]] = []
        self.job = WindowsJob()

    def start(
        self,
        name: str,
        command: list[str],
        *,
        environment: dict[str, str],
        stdout: IO[bytes] | None = None,
        stderr: IO[bytes] | None = None,
    ) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            ),
            start_new_session=os.name != "nt",
        )
        self.job.assign(process)
        self.processes.append((name, process))
        print(f"Started E2E {name} process pid={process.pid}", flush=True)
        return process

    def diagnostics(self) -> list[str]:
        roots = {process.pid for _name, process in self.processes}
        names = {process.pid: name for name, process in self.processes}
        if os.name == "nt":
            return [
                (
                    f"pid={entry.pid} ppid={entry.parent_pid} "
                    f"name={names.get(entry.pid, entry.executable)}"
                )
                for entry in process_descendants(roots)
            ]
        return [
            f"pid={process.pid} name={name} returncode={process.poll()}"
            for name, process in self.processes
            if process.poll() is None
        ]

    def shutdown(self) -> None:
        alive = [
            (name, process)
            for name, process in reversed(self.processes)
            if process.poll() is None
        ]
        for _name, process in alive:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                continue

        deadline = time.monotonic() + self.shutdown_timeout_seconds
        while any(process.poll() is None for _name, process in alive):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)

        remaining = self.diagnostics()
        if remaining:
            print(
                "E2E shutdown timeout; terminating only runner-owned processes:",
                flush=True,
            )
            for line in remaining:
                print(f"  {line}", flush=True)
        self.job.close()

        for _name, process in alive:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    continue
        for _name, process in self.processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


def wait_for_http(
    url: str,
    *,
    timeout_seconds: int,
    watched_processes: list[tuple[str, subprocess.Popen[bytes]]],
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for name, process in watched_processes:
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(
                    f"E2E {name} process exited before readiness with code {return_code}"
                )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for E2E service readiness: {url}")


def print_log_tail(path: Path, *, lines: int = 40) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if content:
        print(f"--- {path.name} (last {lines} lines) ---", flush=True)
        for line in content[-lines:]:
            print(line, flush=True)


def configure_console_output() -> None:
    """Keep diagnostics printable on legacy Windows console code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def main() -> int:
    configure_console_output()
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
            "E2E_EXTERNAL_SERVERS": "true",
            "PYTHON_BINARY": sys.executable,
            "WEB_ORIGIN": "http://localhost:3100",
            "CORS_ORIGINS": (
                "http://localhost:3100,http://localhost:3000,http://localhost:8080"
            ),
        }
    )

    run_timeout = bounded_seconds(
        "E2E_RUN_TIMEOUT_SECONDS",
        DEFAULT_RUN_TIMEOUT_SECONDS,
        minimum=30,
        maximum=900,
    )
    server_timeout = bounded_seconds(
        "E2E_SERVER_TIMEOUT_SECONDS",
        DEFAULT_SERVER_TIMEOUT_SECONDS,
        minimum=10,
        maximum=300,
    )
    shutdown_timeout = bounded_seconds(
        "E2E_SHUTDOWN_TIMEOUT_SECONDS",
        DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        minimum=2,
        maximum=60,
    )
    database_name = app_url.database or ""
    runtime_root = REPOSITORY_ROOT / ".test-artifacts"
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_directory = Path(tempfile.mkdtemp(prefix="e2e-runtime-", dir=runtime_root))
    api_log = runtime_directory / "api.log"
    web_log = runtime_directory / "web.log"
    supervisor = ProcessSupervisor(shutdown_timeout)
    result = 1

    print(
        f"Preparing isolated PostgreSQL database {database_name!r} for E2E tests",
        flush=True,
    )
    try:
        asyncio.run(recreate_database(migration_url, app_url))
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
            timeout=120,
        )

        with (
            api_log.open("wb") as api_output,
            web_log.open("wb") as web_output,
        ):
            api_process = supervisor.start(
                "api",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "teamora_api.main:app",
                    "--app-dir",
                    "services/api",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8100",
                    "--timeout-graceful-shutdown",
                    "5",
                ],
                environment=environment,
                stdout=api_output,
                stderr=subprocess.STDOUT,
            )
            web_process = supervisor.start(
                "web",
                [
                    node_executable(),
                    str(
                        REPOSITORY_ROOT
                        / "node_modules"
                        / "next"
                        / "dist"
                        / "bin"
                        / "next"
                    ),
                    "dev",
                    "apps/web",
                    "-p",
                    "3100",
                ],
                environment={
                    **environment,
                    "API_INTERNAL_URL": "http://127.0.0.1:8100",
                    "NEXT_PUBLIC_REALTIME_URL": (
                        "ws://localhost:8100/api/v1/realtime/ws"
                    ),
                },
                stdout=web_output,
                stderr=subprocess.STDOUT,
            )
            wait_for_http(
                "http://127.0.0.1:8100/api/v1/health/ready",
                timeout_seconds=server_timeout,
                watched_processes=[("api", api_process)],
            )
            wait_for_http(
                "http://localhost:3100",
                timeout_seconds=server_timeout,
                watched_processes=[("web", web_process)],
            )

            playwright = supervisor.start(
                "playwright",
                [
                    node_executable(),
                    str(
                        REPOSITORY_ROOT
                        / "node_modules"
                        / "@playwright"
                        / "test"
                        / "cli.js"
                    ),
                    "test",
                    *arguments.playwright_args,
                ],
                environment=environment,
            )
            try:
                result = playwright.wait(timeout=run_timeout)
            except subprocess.TimeoutExpired:
                print(
                    f"Playwright exceeded the bounded {run_timeout}s timeout.",
                    flush=True,
                )
                for line in supervisor.diagnostics():
                    print(f"  {line}", flush=True)
                result = 124
            if result != 0:
                print_log_tail(api_log)
                print_log_tail(web_log)
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(f"E2E runner failed: {exc}", file=sys.stderr, flush=True)
        print_log_tail(api_log)
        print_log_tail(web_log)
        result = 1
    finally:
        supervisor.shutdown()
        try:
            asyncio.run(drop_database(migration_url, database_name))
            print(f"Removed isolated E2E database {database_name!r}", flush=True)
        finally:
            shutil.rmtree(runtime_directory, ignore_errors=True)

    return result


if __name__ == "__main__":
    raise SystemExit(main())
