import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class IncidentReplayer:
    """Manages the replay and visual verification of previously captured incidents."""

    def __init__(self, runs_dir: str = ".loki/runs"):
        self.runs_dir = Path(runs_dir)

    def get_run_dir(self, run_id: str | None = None) -> Path | None:
        """Resolves target run directory by ID or falls back to the most recent one."""
        if not self.runs_dir.exists():
            return None

        if run_id:
            target = self.runs_dir / run_id
            return target if target.exists() and target.is_dir() else None

        # Sort runs by modification time descending
        run_dirs = [d for d in self.runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]
        if not run_dirs:
            return None
        run_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        return run_dirs[0]

    CRASH_REPRODUCED_MARKERS = [
        "[LOKI REPRO] CRASH SUCCESSFULLY REPRODUCED",
        "[LOKI REPRO] RACE CONDITION REPRODUCED",
        "[LOKI REPRO] CRASH(ES) DETECTED",
    ]

    def replay_test(self, run_dir: Path) -> dict[str, Any]:
        """Executes the generated reproduction script for the incident."""
        repro_script = run_dir / "repro_test.py"
        if not repro_script.exists():
            return {"success": False, "error": f"Reproduction script missing: {repro_script}"}

        # Run with current Python executable in visible mode with UTF-8 encoding
        cmd = [sys.executable, str(repro_script)]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=30,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""

            # Check whether target application error was explicitly reproduced
            has_crash_marker = any(marker in stdout for marker in self.CRASH_REPRODUCED_MARKERS)

            if result.returncode == 0:
                # Script ran cleanly and detected 0 crashes (bug resolved)
                return {
                    "success": True,
                    "reproduced": False,
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": result.returncode,
                }
            elif has_crash_marker:
                # Script executed and successfully confirmed target crash reproduction
                return {
                    "success": True,
                    "reproduced": True,
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": result.returncode,
                }
            else:
                # Script exited with non-zero code, but did NOT confirm crash reproduction.
                # This indicates an unhandled runtime exception, syntax error, or environment issue.
                err_detail = stderr.strip() or stdout.strip() or f"Script exited with status code {result.returncode}"
                return {
                    "success": False,
                    "reproduced": False,
                    "error": f"Reproduction script encountered an execution error: {err_detail}",
                    "stdout": stdout,
                    "stderr": stderr,
                    "returncode": result.returncode,
                }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Replay execution timed out after 30 seconds."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def open_video(self, run_dir: Path) -> dict[str, Any]:
        """Opens the recorded video artifact in the OS default video player."""
        video_file = run_dir / "replay.webm"
        if not video_file.exists():
            return {"success": False, "error": f"Video artifact missing: {video_file}"}

        try:
            if os.name == "nt":  # Windows
                os.startfile(str(video_file.resolve()))
            elif sys.platform == "darwin":  # macOS
                subprocess.Popen(["open", str(video_file.resolve())])
            else:  # Linux
                subprocess.Popen(["xdg-open", str(video_file.resolve())])
            return {"success": True, "video_path": str(video_file)}
        except Exception as e:
            return {"success": False, "error": str(e)}
