"""Infrastructure-level chaos: faults injected against real local processes and
(optionally) Docker containers, instead of browser behavior. This is a different
layer than the personas (`src/loki/personas/`), which only ever act through a
Playwright-driven browser — infra chaos reaches past the browser to the actual
service(s) backing the target, the way Chaos Monkey/Gremlin-style tools do:
killing a dependency mid-request, hanging a process, starving it of CPU/memory.

Scoped to the local machine (processes you can already signal, containers on
your own Docker daemon) — same trust boundary as `localhost` in `safety.py`: if
you can already see/signal it without any extra credentials, it's yours to test.
Reaching a *remote* host's infrastructure (SSH, a remote Docker context, a cloud
API) is a different, far more sensitive capability and is explicitly out of
scope here; see AGENTS.md before ever adding that.
"""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

# Process names that must never be targeted, even if a caller asks by name/port
# collision — killing/pausing these would take down the operator's own machine or
# this very tool, not the thing under test. Matched as a WHOLE process name (minus
# extension), never a bare substring: a naive "init" in name.lower() check also
# protects legitimate targets like 'initdb.exe' (Postgres' own init process) or
# any app with "init"/"systemd" somewhere in its name, which isn't the intent.
_PROTECTED_NAMES = {
    "system", "svchost.exe", "wininit.exe", "winlogon.exe", "csrss.exe", "smss.exe",
    "lsass.exe", "explorer.exe", "dwm.exe", "systemd", "init", "launchd", "kernel_task",
}

# Tracks PIDs of cpu_stress/memory_stress workers while they're deliberately kept
# alive, so a run that gets killed from the outside (crash, taskkill, a supervisor,
# Task Manager "End task" — not just a clean Ctrl+C) can be reconciled afterward
# with `loki infra cleanup` instead of leaving them silently burning CPU/memory.
_STRESS_WORKERS_TRACKING_PATH = Path(".loki/infra_stress_workers.json")
_WORKER_TRACKING_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def _tracking_lock(timeout: float = 5.0):
    """Advisory file lock + thread lock to serialize read-modify-write access
    to the stress workers tracking file across concurrent processes and threads."""
    lock_path = _STRESS_WORKERS_TRACKING_PATH.with_suffix(".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    with _WORKER_TRACKING_THREAD_LOCK:
        start_time = time.time()
        f = None
        try:
            f = open(lock_path, "a+b")
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError, PermissionError):
                    if time.time() - start_time >= timeout:
                        break
                    time.sleep(0.05)
            yield
        finally:
            if f is not None:
                try:
                    if os.name == "nt":
                        import msvcrt
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    f.close()
                except Exception:
                    pass


def _track_workers(pids: List[int]) -> None:
    if not pids:
        return
    with _tracking_lock():
        _STRESS_WORKERS_TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_tracked_workers()
        new_pids = sorted(set(existing) | set(pids))
        temp_path = _STRESS_WORKERS_TRACKING_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(new_pids), encoding="utf-8")
        temp_path.replace(_STRESS_WORKERS_TRACKING_PATH)


def _untrack_workers(pids: List[int]) -> None:
    if not pids:
        return
    with _tracking_lock():
        remaining = [p for p in _read_tracked_workers() if p not in set(pids)]
        if remaining:
            temp_path = _STRESS_WORKERS_TRACKING_PATH.with_suffix(".tmp")
            temp_path.write_text(json.dumps(remaining), encoding="utf-8")
            temp_path.replace(_STRESS_WORKERS_TRACKING_PATH)
        else:
            _STRESS_WORKERS_TRACKING_PATH.unlink(missing_ok=True)


def _read_tracked_workers() -> List[int]:
    if not _STRESS_WORKERS_TRACKING_PATH.exists():
        return []
    try:
        data = json.loads(_STRESS_WORKERS_TRACKING_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [
            int(p)
            for p in data
            if isinstance(p, (int, str))
            and not isinstance(p, bool)
            and str(p).isdigit()
            and int(p) > 0
        ]
    except Exception:
        return []


def cleanup_stress_workers() -> "tuple[List[int], List[int]]":
    """Kills any stress workers left tracked from a run that didn't exit cleanly
    (the parent `loki infra cpu-stress`/`memory-stress` process got killed from
    the outside before its own `finally` cleanup could run). Safe to call anytime
    — PIDs that are already gone are just dropped from the tracking file."""
    with _tracking_lock():
        tracked = _read_tracked_workers()
        killed, already_gone = [], []
        for pid in tracked:
            try:
                psutil.Process(pid).kill()
                killed.append(pid)
            except psutil.NoSuchProcess:
                already_gone.append(pid)
            except psutil.AccessDenied:
                pass
        _STRESS_WORKERS_TRACKING_PATH.unlink(missing_ok=True)
        return killed, already_gone


@dataclass
class InfraActionResult:
    """Outcome of a single infrastructure chaos action, for reporting/evidence."""
    action: str
    target_description: str
    success: bool
    detail: str = ""
    pid: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class ProtectedProcessError(Exception):
    """Raised when a chaos action would target a protected system process or LOKI itself."""


def _is_protected(proc: psutil.Process) -> bool:
    if proc.pid == os.getpid() or proc.pid == os.getppid():
        return True
    try:
        name = (proc.name() or "").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True
    stem = name[:-4] if name.endswith(".exe") else name
    return name in _PROTECTED_NAMES or stem in _PROTECTED_NAMES


def find_processes(pid: Optional[int] = None, port: Optional[int] = None, name: Optional[str] = None) -> List[psutil.Process]:
    """Resolves a target process by exactly one of: PID, listening port, or name substring."""
    if pid is not None:
        try:
            return [psutil.Process(pid)]
        except psutil.NoSuchProcess:
            return []

    if port is not None:
        # LISTEN only — "the service on this port" means something listening there,
        # not any connection that merely happens to be using that number as its own
        # local/ephemeral port (e.g. an outbound client connection), which is a
        # different process entirely and not what "--port" is meant to target.
        matches: List[psutil.Process] = []
        seen_pids = set()
        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.status == psutil.CONN_LISTEN
                and conn.laddr and conn.laddr.port == port
                and conn.pid and conn.pid not in seen_pids
            ):
                try:
                    matches.append(psutil.Process(conn.pid))
                    seen_pids.add(conn.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        return matches

    if name is not None:
        needle = name.lower()
        matches = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if needle in (proc.info.get("name") or "").lower():
                    matches.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return matches

    return []


def kill_process(pid: Optional[int] = None, port: Optional[int] = None, name: Optional[str] = None) -> InfraActionResult:
    """Kills the resolved process(es) outright — simulates a crashed/OOM-killed dependency."""
    target_desc = f"pid={pid}" if pid else (f"port={port}" if port else f"name='{name}'")
    procs = find_processes(pid=pid, port=port, name=name)
    if not procs:
        return InfraActionResult("kill", target_desc, success=False, detail="No matching process found.")

    killed = []
    for proc in procs:
        if _is_protected(proc):
            return InfraActionResult(
                "kill", target_desc, success=False,
                detail=f"Refused: pid {proc.pid} ({_safe_name(proc)}) looks like a protected system process or LOKI itself.",
            )
    for proc in procs:
        try:
            proc_name = _safe_name(proc)
            proc.kill()
            killed.append((proc.pid, proc_name))
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            return InfraActionResult("kill", target_desc, success=False, pid=proc.pid, detail=str(e))

    return InfraActionResult(
        "kill", target_desc, success=True,
        detail=f"Killed {len(killed)} process(es): " + ", ".join(f"{n} (pid {p})" for p, n in killed),
        pid=killed[0][0] if killed else None,
        extra={"killed": killed},
    )


def pause_process(pid: Optional[int] = None, port: Optional[int] = None, name: Optional[str] = None, duration: float = 5.0) -> InfraActionResult:
    """Suspends the resolved process (SIGSTOP on POSIX, NtSuspendProcess on Windows via
    psutil) for `duration` seconds, then resumes it — simulates a hung/unresponsive
    dependency instead of a hard crash. Blocks for the duration of the pause."""
    target_desc = f"pid={pid}" if pid else (f"port={port}" if port else f"name='{name}'")
    procs = find_processes(pid=pid, port=port, name=name)
    if not procs:
        return InfraActionResult("pause", target_desc, success=False, detail="No matching process found.")

    proc = procs[0]
    if _is_protected(proc):
        return InfraActionResult(
            "pause", target_desc, success=False,
            detail=f"Refused: pid {proc.pid} ({_safe_name(proc)}) looks like a protected system process or LOKI itself.",
        )

    proc_name = _safe_name(proc)
    try:
        proc.suspend()
        time.sleep(max(0.0, duration))
        proc.resume()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        # Best-effort: if it died mid-pause, there's nothing left to resume.
        return InfraActionResult("pause", target_desc, success=False, pid=proc.pid, detail=str(e))

    return InfraActionResult(
        "pause", target_desc, success=True,
        detail=f"Suspended '{proc_name}' (pid {proc.pid}) for {duration}s, then resumed it.",
        pid=proc.pid,
    )


def _safe_name(proc: psutil.Process) -> str:
    try:
        return proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "unknown"


def _cpu_burner():
    """Target of a spawned CPU-stress worker subprocess: busy-loops until killed."""
    while True:
        pass


def cpu_stress(duration: float = 5.0, workers: Optional[int] = None) -> InfraActionResult:
    """Spawns `workers` (default: one per CPU core) busy-looping subprocesses for
    `duration` seconds to saturate every core, then kills them — simulates a noisy
    neighbor or a dependency pegging the CPU, without touching any specific process."""
    # `workers or default` would silently replace an explicit 0 with the default,
    # since 0 is falsy — only fall back to the default when workers is None.
    worker_count = workers if workers is not None else (os.cpu_count() or 2)
    if worker_count <= 0:
        return InfraActionResult("cpu_stress", f"{worker_count} worker(s)", success=True, detail="0 workers requested — nothing to do.")

    procs = []
    try:
        for _ in range(worker_count):
            p = subprocess.Popen(
                [sys.executable, "-c", "while True: pass"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            procs.append(p)
        # Tracked *before* sleeping: if this process gets killed from the outside
        # during the sleep, `loki infra cleanup` can still find and kill these.
        _track_workers([p.pid for p in procs])

        dead_on_arrival = [p.pid for p in procs if p.poll() is not None]
        time.sleep(max(0.0, duration))
    finally:
        for p in procs:
            try:
                p.kill()
                p.wait(timeout=3)
            except Exception:
                pass
        _untrack_workers([p.pid for p in procs])

    if dead_on_arrival:
        return InfraActionResult(
            "cpu_stress", f"{worker_count} worker(s)", success=False,
            detail=f"{len(dead_on_arrival)}/{worker_count} worker(s) exited immediately instead of running — "
                   f"stress was not fully applied.",
            extra={"workers": worker_count, "dead_on_arrival": dead_on_arrival},
        )

    return InfraActionResult(
        "cpu_stress", f"{worker_count} worker(s)", success=True,
        detail=f"Saturated {worker_count} CPU worker(s) for {duration}s.",
        extra={"workers": worker_count},
    )


def memory_stress(duration: float = 5.0, megabytes: int = 512) -> InfraActionResult:
    """Spawns a subprocess that allocates and holds `megabytes` of memory for
    `duration` seconds, then releases it — simulates memory pressure from a noisy
    neighbor or a leak in a dependency, without touching any specific process."""
    script = (
        "import time, sys\n"
        f"buf = bytearray({megabytes} * 1024 * 1024)\n"
        "for i in range(0, len(buf), 4096):\n"
        "    buf[i] = 1\n"  # touch every page so the OS actually commits it, not just reserves it
        f"time.sleep({max(0.0, duration)})\n"
    )
    p = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    _track_workers([p.pid])
    try:
        _, stderr = p.communicate(timeout=duration + 10)
        if p.returncode == 0:
            success = True
            detail = f"Held {megabytes}MB resident for {duration}s."
        else:
            # The worker crashed (e.g. a negative/absurdly large --mb raising
            # ValueError/MemoryError) — this must NOT be reported as success.
            success = False
            detail = f"Worker exited with code {p.returncode} instead of holding the memory: {(stderr or '').strip()[-300:]}"
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=5)
        success = False
        detail = "Memory stress worker didn't exit cleanly within the expected window; killed it."
    except Exception as e:
        success = False
        detail = str(e)
    finally:
        _untrack_workers([p.pid])

    return InfraActionResult("memory_stress", f"{megabytes}MB", success=success, detail=detail, extra={"megabytes": megabytes})


# --- Optional Docker container backend -------------------------------------

def docker_available() -> bool:
    """True if a local `docker` CLI is installed and its daemon is reachable."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def kill_container(name: str) -> InfraActionResult:
    """Kills (SIGKILL, container stays stopped) a local Docker container by name/ID."""
    if not docker_available():
        return InfraActionResult("kill_container", name, success=False, detail="Docker isn't installed or the daemon isn't reachable.")
    result = subprocess.run(["docker", "kill", name], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return InfraActionResult("kill_container", name, success=False, detail=result.stderr.strip() or result.stdout.strip())
    return InfraActionResult("kill_container", name, success=True, detail=f"Killed container '{name}'.")


def pause_container(name: str, duration: float = 5.0) -> InfraActionResult:
    """Pauses (cgroup freeze, not a signal) a local Docker container for `duration`
    seconds, then unpauses it — simulates a hung dependency container."""
    if not docker_available():
        return InfraActionResult("pause_container", name, success=False, detail="Docker isn't installed or the daemon isn't reachable.")
    result = subprocess.run(["docker", "pause", name], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return InfraActionResult("pause_container", name, success=False, detail=result.stderr.strip() or result.stdout.strip())
    time.sleep(max(0.0, duration))
    result = subprocess.run(["docker", "unpause", name], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return InfraActionResult(
            "pause_container", name, success=False,
            detail=f"Paused but failed to unpause: {result.stderr.strip() or result.stdout.strip()}. Run `docker unpause {name}` manually.",
        )
    return InfraActionResult("pause_container", name, success=True, detail=f"Paused container '{name}' for {duration}s, then unpaused it.")
