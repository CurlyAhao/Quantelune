"""Windows source preview: own paths, no installation, identity-checked process stop."""

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "q" / "pv"
RUNTIME = STATE / "runtime.json"


def clean_env(web_port: int, api_port: int, instance: str) -> dict[str, str]:
    env = {
        k: os.environ[k]
        for k in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "COMSPEC")
        if k in os.environ
    }
    env.update(
        {
            "TEMP": str(STATE / "t"),
            "TMP": str(STATE / "t"),
            "NODE_ENV": "development",
            "NEXT_TELEMETRY_DISABLED": "1",
            "OTEL_SDK_DISABLED": "true",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "STATFORGE_PREVIEW_INSTANCE": instance,
            "NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{api_port}/api/v1",
            "NEXT_PUBLIC_PREVIEW_WEB_PORT": str(web_port),
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return env


def check_inputs(api_port: int, web_port: int) -> tuple[Path, str]:
    if api_port == web_port or not all(1024 <= p <= 65535 for p in (api_port, web_port)):
        raise RuntimeError("PREVIEW_PORT_INVALID")
    dotenv = [
        p.name
        for p in (ROOT / "apps/web").glob(".env*")
        if p.is_file() and p.name not in {".env.example", ".env.sample", ".env.template"}
    ]
    if dotenv:
        raise RuntimeError("PREVIEW_WEB_DOTENV_PRESENT")
    python = ROOT / "apps/api/.workspace-venv/Scripts/python.exe"
    if not python.is_file():
        python = ROOT / "apps/api/.venv/Scripts/python.exe"
    node = shutil.which("node")
    if (
        not python.is_file()
        or not node
        or not (ROOT / "apps/web/node_modules/next/dist/bin/next").is_file()
    ):
        raise RuntimeError("PREVIEW_FROZEN_DEPENDENCIES_REQUIRED")
    for port in (api_port, web_port):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError("PREVIEW_PORT_IN_USE") from None
    owner = STATE / "owner.json"
    if owner.exists():
        if json.loads(owner.read_text(encoding="utf-8"))["worktree"] != str(ROOT):
            raise RuntimeError("PREVIEW_STATE_OWNER_INVALID")
    elif (STATE / "db.sqlite").exists():
        raise RuntimeError("PREVIEW_EXISTING_DB_REQUIRES_REVIEW")
    return python, node


def save(value: dict) -> None:
    path = STATE / f"runtime.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    path.replace(RUNTIME)


def process_rows(pid: int, children: bool = False) -> list[dict]:
    # Narrow PID/parent query, never inventory unrelated command lines/environment.
    selector = "ParentProcessId" if children else "ProcessId"
    code = (
        f"$sfWorktree = '{str(ROOT).replace(chr(39), chr(39) * 2)}'; "
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
        f"@(Get-CimInstance Win32_Process -Filter '{selector} = {int(pid)}' | "
        "Where-Object { $_.ProcessId -ne $PID } | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine) | "
        "ConvertTo-Json -Compress"
    )
    raw = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if not raw.strip():
        return []
    value = json.loads(raw)
    return value if isinstance(value, list) else [value]


@contextmanager
def handle(pid: int):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    owned = kernel.OpenProcess(0x1000 | 0x0001 | 0x100000, False, pid)
    if not owned:
        if ctypes.get_last_error() != 87:  # only ERROR_INVALID_PARAMETER means absent PID
            raise RuntimeError("PREVIEW_PROCESS_ACCESS_DENIED")
        yield None, None
        return
    try:
        yield kernel, owned
    finally:
        kernel.CloseHandle(owned)


def created(kernel, owned) -> int:
    values = [ctypes.c_ulonglong() for _ in range(4)]
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    if not kernel.GetProcessTimes(owned, *(ctypes.byref(v) for v in values)):
        raise RuntimeError("PREVIEW_PROCESS_IDENTITY_UNAVAILABLE")
    return values[0].value


def image(kernel, owned) -> str:
    buffer = ctypes.create_unicode_buffer(32768)
    size = ctypes.c_ulong(len(buffer))
    kernel.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    if not kernel.QueryFullProcessImageNameW(owned, 0, buffer, ctypes.byref(size)):
        raise RuntimeError("PREVIEW_PROCESS_IDENTITY_UNAVAILABLE")
    return buffer.value


def ended(kernel, owned, timeout: int = 0) -> bool:
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    result = kernel.WaitForSingleObject(owned, timeout)
    if result == 0:
        return True
    if result == 258:
        return False
    raise RuntimeError("PREVIEW_PROCESS_WAIT_FAILED")


def live_identity(pid: int, kernel, owned, parent=None) -> dict | None:
    """One opened object, live checks around a bounded metadata re-read."""
    if ended(kernel, owned) or (parent and ended(parent[0], parent[1])):
        return None
    stamp, executable = created(kernel, owned), image(kernel, owned)
    for _attempt in range(2):
        if ended(kernel, owned) or (parent and ended(parent[0], parent[1])):
            return None
        rows = process_rows(pid)
        if ended(kernel, owned) or (parent and ended(parent[0], parent[1])):
            return None
        if rows:
            row = rows[0]
            if row.get("ExecutablePath") and row.get("CommandLine"):
                if str(Path(row["ExecutablePath"]).resolve()).casefold() != str(Path(executable).resolve()).casefold():
                    raise RuntimeError("PREVIEW_PROCESS_IDENTITY_CHANGED")
                if created(kernel, owned) != stamp or image(kernel, owned) != executable:
                    raise RuntimeError("PREVIEW_PROCESS_IDENTITY_CHANGED")
                row["creation_ticks"] = stamp
                return row
    raise RuntimeError("PREVIEW_LIVE_METADATA_UNAVAILABLE")


def record(pid: int) -> dict | None:
    with handle(pid) as (kernel, owned):
        return live_identity(pid, kernel, owned) if owned else None


def matches(row: dict, other: dict) -> bool:
    return all(
        row[k] == other[k] for k in ("ProcessId", "ExecutablePath", "CommandLine", "creation_ticks")
    )


def auxiliary(row: dict) -> bool:
    """OS console hosts and our narrow CIM readers are never terminated."""
    system = Path(os.environ["SYSTEMROOT"]) / "System32"
    exe = (row.get("ExecutablePath") or "").casefold()
    if exe == str(system / "conhost.exe").casefold():
        return True
    command = row.get("CommandLine") or ""
    return (
        exe == str(system / "WindowsPowerShell/v1.0/powershell.exe").casefold()
        and "Get-CimInstance Win32_Process -Filter" in command
        and "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine" in command
    )


def assert_source(row: dict, instance: str | None = None) -> None:
    command = (row.get("CommandLine") or "").casefold()
    if str(ROOT).casefold() not in command:
        raise RuntimeError("PREVIEW_UNKNOWN_CHILD_PRESERVED")
    if instance and any(name in command for name in ("preview_entry.py", "preview_launcher.py")):
        if instance not in command:
            raise RuntimeError("PREVIEW_PROCESS_IDENTITY_CHANGED")


def owned_descendants(parents: list[dict], instance: str | None = None) -> list[dict]:
    found = []
    for expected in parents:
        pid = expected["ProcessId"]
        with handle(pid) as (pk, ph):
            parent = live_identity(pid, pk, ph) if ph else None
            if parent is None:
                continue
            if not matches(expected, parent):
                raise RuntimeError("PREVIEW_PROCESS_IDENTITY_CHANGED")
            assert_source(parent, instance)
            # CIM snapshot fields are never authorization evidence for a child.
            for candidate in process_rows(pid, children=True):
                with handle(candidate["ProcessId"]) as (ck, ch):
                    exact = live_identity(candidate["ProcessId"], ck, ch, (pk, ph)) if ch else None
                    if exact is None:
                        continue
                    if ended(pk, ph) or ended(ck, ch):
                        continue
                    if (
                        exact["ParentProcessId"] != pid
                        or exact["creation_ticks"] < parent["creation_ticks"]
                    ):
                        raise RuntimeError("PREVIEW_PROCESS_IDENTITY_CHANGED")
                    if auxiliary(exact):
                        # Preserve the complete system host identity; do not recurse or terminate.
                        continue
                    assert_source(exact, instance)
                    exact["owner_parent"] = parent
                    found.append(exact)
                    found.extend(owned_descendants([exact], instance))
    return found


def terminate_wait(kernel, owned) -> None:
    if ended(kernel, owned):
        return
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    if not kernel.TerminateProcess(owned, 0) and not ended(kernel, owned):
        raise RuntimeError("PREVIEW_STOP_FAILED")
    if not ended(kernel, owned, 5000):
        raise RuntimeError("PREVIEW_STOP_PENDING")


def ports_released(state: dict) -> bool:
    for field in ("api_url", "web_url"):
        url = urlsplit(state[field])
        if url.hostname != "127.0.0.1" or url.port is None:
            raise RuntimeError("PREVIEW_STATE_OWNER_INVALID")
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", url.port)) == 0:
                return False
    return True


def stop() -> None:
    if not RUNTIME.is_file():
        print("No recorded preview instance. Data preserved.")
        return
    state = json.loads(RUNTIME.read_text(encoding="utf-8"))
    if state["worktree"] != str(ROOT):
        raise RuntimeError("PREVIEW_STATE_OWNER_INVALID")
    retained = []
    writer_quiet = False
    try:
        with ExitStack() as stack:
            live = []
            for prior in state.get("processes", []):
                kernel, owned = stack.enter_context(handle(prior["ProcessId"]))
                current = live_identity(prior["ProcessId"], kernel, owned) if owned else None
                if current is None:
                    continue
                if not matches(prior, current):
                    raise RuntimeError("PREVIEW_PROCESS_IDENTITY_CHANGED")
                if auxiliary(current):
                    retained.append(current["ProcessId"])
                    continue
                assert_source(current, state["instance"])
                live.append((current, kernel, owned))
            # Quiesce only the verified source supervisor writers first; keep their
            # stable handles open. No stale producer can overwrite the final state.
            for row, kernel, owned in reversed(live):
                if "preview_launcher.py" in row["CommandLine"] and " serve " in row["CommandLine"]:
                    terminate_wait(kernel, owned)
            writer_quiet = True
            state["phase"] = "stop_pending"
            save(state)
            parents = [row for row, kernel, owned in live if not ended(kernel, owned)]
            seen = {row["ProcessId"] for row, _kernel, _owned in live}
            for child in owned_descendants(parents, state["instance"]):
                if child["ProcessId"] in seen:
                    continue
                kernel, owned = stack.enter_context(handle(child["ProcessId"]))
                current = live_identity(child["ProcessId"], kernel, owned) if owned else None
                if current is None:
                    continue
                if not matches(child, current):
                    raise RuntimeError("PREVIEW_PROCESS_IDENTITY_CHANGED")
                seen.add(child["ProcessId"])
                live.append((current, kernel, owned))
            for _row, kernel, owned in reversed(live):
                terminate_wait(kernel, owned)
            deadline = time.monotonic() + 5
            while not ports_released(state):
                if time.monotonic() >= deadline:
                    raise RuntimeError("PREVIEW_PORT_RELEASE_PENDING")
                time.sleep(0.1)
            state["phase"] = "stopped"
            state["automatic_hosts_not_terminated"] = retained
            save(state)
    except Exception:
        if writer_quiet:
            state["phase"] = "stop_pending"
            state["automatic_hosts_not_terminated"] = retained
            save(state)
        raise
    print(
        "Verified source servers stopped. Automatic console/CIM hosts are not terminated; "
        "data preserved."
    )


def serve(api_port: int, web_port: int, instance: str) -> None:
    import msvcrt

    python, node = check_inputs(api_port, web_port)
    STATE.mkdir(parents=True, exist_ok=True)
    for name in ("s", "t", "l"):
        (STATE / name).mkdir(exist_ok=True)
    with (STATE / "instance.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        owner = STATE / "owner.json"
        if not owner.exists():
            owner.write_text(json.dumps({"worktree": str(ROOT), "schema": 1}), encoding="utf-8")
        # Refuse old schemas: this preview does not run migrations on user DBs.
        if (STATE / "db.sqlite").exists():
            import sqlite3

            with closing(sqlite3.connect(
                f"file:{(STATE / 'db.sqlite').as_posix()}?mode=ro", uri=True
            )) as db:
                columns = {r[1] for r in db.execute("PRAGMA table_info(preview_turns)")}
                if "authorization_json" not in columns:
                    raise RuntimeError("PREVIEW_SCHEMA_REVIEW_REQUIRED")
        self_record = record(os.getpid())
        if self_record is None:
            raise RuntimeError("PREVIEW_PROCESS_IDENTITY_UNAVAILABLE")
        # Windows venv python.exe is a redirector; record its verified parent
        # too, rather than leaving a launcher/console process outside Stop.
        redirector = record(self_record["ParentProcessId"])
        processes = [self_record]
        if (
            redirector
            and redirector["ExecutablePath"].casefold() == str(python).casefold()
            and str(Path(__file__).resolve()).casefold() in redirector["CommandLine"].casefold()
            and instance in redirector["CommandLine"]
            and redirector["creation_ticks"] <= self_record["creation_ticks"]
        ):
            processes.insert(0, redirector)
        state = {
            "worktree": str(ROOT),
            "instance": instance,
            "phase": "starting",
            "api_url": f"http://127.0.0.1:{api_port}/api/v1",
            "web_url": f"http://127.0.0.1:{web_port}",
            "processes": processes,
        }
        save(state)
        with (
            (STATE / "l/api.stdout.log").open("ab") as api_log,
            (STATE / "l/web.stdout.log").open("ab") as web_log,
        ):
            api = subprocess.Popen(
                [
                    str(python),
                    "-I",
                    str(ROOT / "apps/api/preview_entry.py"),
                    "--instance",
                    instance,
                    "--api-port",
                    str(api_port),
                    "--web-port",
                    str(web_port),
                ],
                cwd=ROOT / "apps/api",
                env=clean_env(web_port, api_port, instance),
                stdout=api_log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            api_record = record(api.pid)
            if api_record is None:
                raise RuntimeError("PREVIEW_API_START_FAILED")
            state["processes"].append(api_record)
            save(state)
            web = subprocess.Popen(
                [
                    node,
                    str(ROOT / "apps/web/node_modules/next/dist/bin/next"),
                    "dev",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(web_port),
                ],
                cwd=ROOT / "apps/web",
                env=clean_env(web_port, api_port, instance),
                stdout=web_log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            web_record = record(web.pid)
            if web_record is None:
                raise RuntimeError("PREVIEW_WEB_START_FAILED")
            state["processes"].append(web_record)
            state["phase"] = "running"
            save(state)
            while api.poll() is None and web.poll() is None:
                time.sleep(1)
                known = {p["ProcessId"] for p in state["processes"]}
                for child in owned_descendants([api_record, web_record], instance):
                    if child["ProcessId"] not in known:
                        state["processes"].append(child)
                        known.add(child["ProcessId"])
                save(state)
            state["phase"] = "process_exited"
            save(state)
            # Stop remains the only identity-checked cleanup entry, preserving data.


def start(api_port: int, web_port: int) -> None:
    python, _node = check_inputs(api_port, web_port)
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "l").mkdir(exist_ok=True)
    (STATE / "t").mkdir(exist_ok=True)
    instance = str(uuid.uuid4())
    with (STATE / "l/supervisor.stdout.log").open("ab") as output:
        supervisor = subprocess.Popen(
            [
                str(python),
                "-I",
                str(Path(__file__).resolve()),
                "serve",
                "--instance",
                instance,
                "--api-port",
                str(api_port),
                "--web-port",
                str(web_port),
            ],
            cwd=ROOT,
            env=clean_env(web_port, api_port, instance),
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if supervisor.poll() is not None:
            raise RuntimeError("PREVIEW_START_FAILED_REVIEW_OWN_LOG")
        try:
            with opener.open(
                f"http://127.0.0.1:{api_port}/api/v1/preview/runtime", timeout=2
            ) as reply:
                ready = json.load(reply)
            with opener.open(f"http://127.0.0.1:{web_port}/login", timeout=10) as reply:
                assert reply.status == 200
            if ready["instance"] == instance:
                print(json.dumps(ready))
                return
        except (OSError, ValueError, AssertionError):
            time.sleep(0.5)
    raise RuntimeError("PREVIEW_START_TIMEOUT_USE_OWN_STOP")


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("PREVIEW_WINDOWS_ONLY")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "serve", "stop"])
    parser.add_argument("--api-port", type=int, default=8134)
    parser.add_argument("--web-port", type=int, default=3134)
    parser.add_argument("--instance")
    args = parser.parse_args()
    if args.action == "stop":
        stop()
    elif args.action == "start":
        start(args.api_port, args.web_port)
    else:
        uuid.UUID(args.instance)
        serve(args.api_port, args.web_port, args.instance)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print environment, provider/configuration values or diagnostics.
        print(
            "Preview operation refused: " + (str(error) if isinstance(error, RuntimeError) and str(error).startswith("PREVIEW_") else type(error).__name__) + "; inspect this instance's local startup logs.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
