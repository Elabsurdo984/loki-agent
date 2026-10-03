import os
from pathlib import Path
from typing import Any
from src.loki.engine.sandbox import IncidentReport


class CIGate:
    """Manages continuous integration execution, exit codes, and GitHub Actions step summaries."""

    @staticmethod
    def is_ci_environment() -> bool:
        """Detects if the process is executing inside a CI/CD environment (GitHub Actions, GitLab CI, etc.)."""
        ci_val = os.environ.get("CI", "").strip().lower()
        return ci_val in ["true", "1", "yes"]

    @staticmethod
    def write_github_step_summary(
        report: IncidentReport,
        evaluations: list[dict[str, Any]] | None,
        run_dir: Path | None,
        has_violations: bool,
    ):
        """Appends a formatted GitHub Markdown summary to $GITHUB_STEP_SUMMARY if available."""
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not summary_path:
            return

        failed = report.has_crashes or has_violations
        status_badge = "❌ **FAILED**" if failed else "✔ **PASSED**"

        lines = [
            f"## ⚡ LOKI Quality & Chaos Gate — {status_badge}\n",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| **Target URL** | `{report.target_url}` |",
            f"| **Active Persona** | `{report.persona_name or 'Passive Observer'}` |",
        ]
        if report.device_name:
            lines.append(f"| **Emulated Device** | 📱 `{report.device_name} ({report.orientation})` |")
        lines.extend([
            f"| **Duration** | `{report.duration_seconds}s` |",
            f"| **Crashes Detected** | `{len(report.crashes) + len(report.http_errors)}` |",
            f"| **Actions Executed** | `{len(report.actions_taken)}` |\n",
        ])

        if report.layout_issues:
            lines.append("### 📱 Mobile & Responsive Layout Anomalies\n")
            for issue in report.layout_issues:
                lines.append(f"- ⚠️ {issue}")
            lines.append("")

        if evaluations:
            lines.append("### 📋 Business Rules Verification\n")
            lines.append("| Assertion | Status | Observation / Evidence |")
            lines.append("| :--- | :---: | :--- |")
            for ev in evaluations:
                st = ev.get("status", "UNKNOWN").upper()
                st_icon = "✔ PASSED" if st == "PASSED" else ("❌ VIOLATED" if st == "VIOLATED" else f"⚠ {st}")
                lines.append(f"| {ev.get('rule')} | **{st_icon}** | {ev.get('observation')} |")
            lines.append("")

        if report.has_crashes:
            lines.append("### 💥 Detected Crashes & Failures\n")
            for crash in set(report.crashes):
                lines.append(f"- 🔴 `{crash}`")
            for http_err in report.http_errors:
                lines.append(f"- 🔴 `{http_err}`")
            lines.append("")

        if run_dir:
            lines.append(
                f"> 📦 Artifacts saved in `{run_dir.name}` (includes `report.html`, `network.har`, and `replay.webm`).\n"
            )

        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass
