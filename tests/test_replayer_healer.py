from pathlib import Path
from unittest.mock import MagicMock
import subprocess

from loki.engine.replayer import IncidentReplayer
from loki.engine.healer import CodeHealer


class TestIncidentReplayer:
    def test_missing_repro_script(self, tmp_path):
        replayer = IncidentReplayer(runs_dir=str(tmp_path))
        res = replayer.replay_test(tmp_path)
        assert res["success"] is False
        assert "Reproduction script missing" in res["error"]

    def test_successful_clean_run_no_crashes(self, tmp_path, monkeypatch):
        script = tmp_path / "repro_test.py"
        script.write_text("print('All good')", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "✔ [LOKI REPRO] No crashes detected. Bug might be resolved."
        mock_proc.stderr = ""
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_proc))

        replayer = IncidentReplayer(runs_dir=str(tmp_path))
        res = replayer.replay_test(tmp_path)
        assert res["success"] is True
        assert res["reproduced"] is False

    def test_genuine_crash_reproduced(self, tmp_path, monkeypatch):
        script = tmp_path / "repro_test.py"
        script.write_text("print('Crash')", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "💥 [LOKI REPRO] CRASH SUCCESSFULLY REPRODUCED!\n  • TypeError: null is not an object"
        mock_proc.stderr = ""
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_proc))

        replayer = IncidentReplayer(runs_dir=str(tmp_path))
        res = replayer.replay_test(tmp_path)
        assert res["success"] is True
        assert res["reproduced"] is True

    def test_internal_script_failure_not_reported_as_reproduced(self, tmp_path, monkeypatch):
        script = tmp_path / "repro_test.py"
        script.write_text("raise RuntimeError()", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "⚡ [LOKI REPRO] Navigating to target..."
        mock_proc.stderr = "Traceback (most recent call last):\n  File 'repro_test.py', line 12, in <module>\nplaywright._impl._errors.TimeoutError: Page.goto timeout"
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_proc))

        replayer = IncidentReplayer(runs_dir=str(tmp_path))
        res = replayer.replay_test(tmp_path)
        # Crucial invariant: Must NOT report reproduced=True
        assert res["success"] is False
        assert res["reproduced"] is False
        assert "Reproduction script encountered an execution error" in res["error"]

    def test_script_exit_code_2_error(self, tmp_path, monkeypatch):
        script = tmp_path / "repro_test.py"
        script.write_text("error", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.returncode = 2
        mock_proc.stdout = ""
        mock_proc.stderr = "❌ [LOKI REPRO ERROR] Test script execution failed: Browser closed"
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=mock_proc))

        replayer = IncidentReplayer(runs_dir=str(tmp_path))
        res = replayer.replay_test(tmp_path)
        assert res["success"] is False
        assert res["reproduced"] is False


class TestCodeHealerVerifyFix:
    def test_verify_fix_success_removes_backup(self, tmp_path):
        repro_script = tmp_path / "repro_test.py"
        repro_script.write_text("# dummy", encoding="utf-8")
        target = tmp_path / "app.py"
        target.write_text("x = 1", encoding="utf-8")
        backup = tmp_path / "app.py.loki.bak"
        backup.write_text("x = 0", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path))
        mock_replayer = MagicMock()
        mock_replayer.replay_test.return_value = {
            "success": True,
            "reproduced": False,
            "stdout": "✔ [LOKI REPRO] No crashes detected.",
        }
        healer.replayer = mock_replayer

        res = healer.verify_fix(tmp_path, backup_file=backup, target_file=target)
        assert res["verified"] is True
        assert not backup.exists()
        assert target.read_text(encoding="utf-8") == "x = 1"

    def test_verify_fix_reproduced_rolls_back(self, tmp_path):
        repro_script = tmp_path / "repro_test.py"
        repro_script.write_text("# dummy", encoding="utf-8")
        target = tmp_path / "app.py"
        target.write_text("fixed_code_still_crashes()", encoding="utf-8")
        backup = tmp_path / "app.py.loki.bak"
        backup.write_text("original_broken_code()", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path))
        mock_replayer = MagicMock()
        mock_replayer.replay_test.return_value = {
            "success": True,
            "reproduced": True,
            "stdout": "💥 [LOKI REPRO] CRASH SUCCESSFULLY REPRODUCED!",
        }
        healer.replayer = mock_replayer

        res = healer.verify_fix(tmp_path, backup_file=backup, target_file=target)
        assert res["verified"] is False
        assert res["rolled_back"] is True
        # Target must be restored from backup
        assert target.read_text(encoding="utf-8") == "original_broken_code()"
        assert not backup.exists()

    def test_verify_fix_does_not_rollback_on_script_execution_error(self, tmp_path):
        repro_script = tmp_path / "repro_test.py"
        repro_script.write_text("# dummy", encoding="utf-8")
        target = tmp_path / "app.py"
        target.write_text("valid_patch_applied()", encoding="utf-8")
        backup = tmp_path / "app.py.loki.bak"
        backup.write_text("original_unpatched_code()", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path))
        mock_replayer = MagicMock()
        mock_replayer.replay_test.return_value = {
            "success": False,
            "reproduced": False,
            "error": "Reproduction script encountered an execution error: Page.goto timeout",
        }
        healer.replayer = mock_replayer

        res = healer.verify_fix(tmp_path, backup_file=backup, target_file=target)
        # Crucial invariant: Must NOT roll back when replay failed to run cleanly
        assert res["verified"] is False
        assert res["rolled_back"] is False
        assert res.get("error") is True
        assert "[REPLAY ERROR]" in res["message"]
        # Source code remains patched and backup remains intact
        assert target.read_text(encoding="utf-8") == "valid_patch_applied()"
        assert backup.exists()


class TestCodeHealerSecurityContainment:
    def test_resolve_source_file_rejects_parent_traversal(self, tmp_path, monkeypatch):
        # Create an outside file in parent directory
        outside_file = tmp_path.parent / "sensitive_secret.py"
        outside_file.write_text("SECRET_KEY = 'leak'", encoding="utf-8")

        try:
            # Change working directory to tmp_path so it acts as repo root
            monkeypatch.chdir(tmp_path)
            healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))

            incident = {
                "crashes": [
                    "Error in ../sensitive_secret.py: line 10 in <module>",
                ]
            }

            resolved = healer.resolve_source_file(incident)
            # Crucial invariant: Must NEVER return a path outside current repository root
            assert resolved is None or resolved.resolve().is_relative_to(tmp_path.resolve())
            if resolved is not None:
                assert resolved.resolve() != outside_file.resolve()
        finally:
            if outside_file.exists():
                outside_file.unlink()

    def test_resolve_source_file_accepts_internal_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        internal_file = tmp_path / "src" / "component.jsx"
        internal_file.parent.mkdir(parents=True, exist_ok=True)
        internal_file.write_text("export default function() {}", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        incident = {
            "crashes": [
                "Uncaught TypeError in src/component.jsx: line 5",
            ]
        }
        resolved = healer.resolve_source_file(incident)
        assert resolved is not None
        assert resolved.resolve() == internal_file.resolve()

    def test_apply_patch_rejects_path_traversal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        outside_file = tmp_path.parent / "outside_victim.py"
        outside_file.write_text("val = 1", encoding="utf-8")

        try:
            healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
            traversal_path = Path("../outside_victim.py")

            res = healer.apply_patch(
                target_file=traversal_path,
                original_snippet="val = 1",
                replacement_snippet="val = 2",
            )
            assert res["success"] is False
            assert "outside the repository root" in res["error"]
            # Ensure outside file was never touched
            assert outside_file.read_text(encoding="utf-8") == "val = 1"
        finally:
            if outside_file.exists():
                outside_file.unlink()

    def test_rollback_rejects_path_traversal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        outside_file = tmp_path.parent / "outside_victim.py"
        backup_file = tmp_path / "backup.loki.bak"
        backup_file.write_text("malicious content", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        res = healer.rollback(target_file=Path("../outside_victim.py"), backup_file=backup_file)
        assert res is False
        assert backup_file.exists()


class TestCodeHealerPatchApplication:
    def test_apply_patch_exact_match_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "app.py"
        target.write_text("def run():\n    crash_here()\n    return 0\n", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        res = healer.apply_patch(
            target_file=target,
            original_snippet="    crash_here()",
            replacement_snippet="    safe_call()",
        )
        assert res["success"] is True
        assert target.read_text(encoding="utf-8") == "def run():\n    safe_call()\n    return 0\n"
        assert (tmp_path / "app.py.loki.bak").exists()

    def test_apply_patch_exact_match_ambiguous_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "app.py"
        initial_content = "def f1():\n    return False\ndef f2():\n    return False\n"
        target.write_text(initial_content, encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        res = healer.apply_patch(
            target_file=target,
            original_snippet="    return False",
            replacement_snippet="    return True",
        )
        assert res["success"] is False
        assert "ambiguous" in res["error"]
        assert "2 exact occurrences" in res["error"]
        # Code must NOT be touched
        assert target.read_text(encoding="utf-8") == initial_content
        assert not (tmp_path / "app.py.loki.bak").exists()

    def test_apply_patch_fuzzy_match_success_with_internal_blank_lines(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "app.py"
        initial_content = (
            "def worker():\n"
            "    # Init\n"
            "    count = 0\n"
            "\n"
            "    process(count)\n"
            "    return True\n"
        )
        target.write_text(initial_content, encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        # Patch with different indentation
        orig_snippet = "count = 0\n\nprocess(count)"
        repl_snippet = "count = 0\n\nsafe_process(count)"

        res = healer.apply_patch(
            target_file=target,
            original_snippet=orig_snippet,
            replacement_snippet=repl_snippet,
        )
        assert res["success"] is True
        new_text = target.read_text(encoding="utf-8")
        assert "safe_process(count)" in new_text
        assert "def worker():" in new_text
        assert "return True" in new_text

    def test_apply_patch_fuzzy_match_ambiguous_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "app.py"
        initial_content = (
            "def a():\n"
            "    x = 1\n"
            "    y = 2\n"
            "def b():\n"
            "        x = 1\n"
            "        y = 2\n"
        )
        target.write_text(initial_content, encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        res = healer.apply_patch(
            target_file=target,
            original_snippet="  x = 1\n  y = 2",
            replacement_snippet="  x = 10\n  y = 20",
        )
        assert res["success"] is False
        assert "ambiguous" in res["error"]
        assert "2 fuzzy matches" in res["error"]
        assert target.read_text(encoding="utf-8") == initial_content

    def test_apply_patch_empty_snippet_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "app.py"
        target.write_text("x = 1\n", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        res = healer.apply_patch(target_file=target, original_snippet="   ", replacement_snippet="x = 2")
        assert res["success"] is False
        assert "empty" in res["error"]
        assert target.read_text(encoding="utf-8") == "x = 1\n"

    def test_apply_patch_snippet_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "app.py"
        target.write_text("x = 1\n", encoding="utf-8")

        healer = CodeHealer(runs_dir=str(tmp_path / ".loki/runs"))
        res = healer.apply_patch(
            target_file=target,
            original_snippet="non_existent_function()",
            replacement_snippet="safe()",
        )
        assert res["success"] is False
        assert "could not be located" in res["error"]


