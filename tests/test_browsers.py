import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from loki.cli import app, BrowserChoice
from loki.engine.ci import CIGate
from loki.engine.html_reporter import HTMLReporter
from loki.engine.recorder import JourneyRecorder
from loki.engine.reporter import IncidentReporter
from loki.engine.sandbox import ChaosSandbox, IncidentReport


def test_browser_choice_enum():
    assert BrowserChoice.CHROMIUM == "chromium"
    assert BrowserChoice.FIREFOX == "firefox"
    assert BrowserChoice.WEBKIT == "webkit"


def test_chaos_sandbox_browser_normalization(tmp_path: Path):
    sb_default = ChaosSandbox(output_dir=str(tmp_path))
    assert sb_default.browser_name == "chromium"

    sb_ff = ChaosSandbox(output_dir=str(tmp_path), browser_name="firefox")
    assert sb_ff.browser_name == "firefox"

    sb_wk = ChaosSandbox(output_dir=str(tmp_path), browser_name="WEBKIT")
    assert sb_wk.browser_name == "webkit"

    sb_invalid = ChaosSandbox(output_dir=str(tmp_path), browser_name="opera")
    assert sb_invalid.browser_name == "chromium"


def test_incident_report_browser_field():
    report = IncidentReport(target_url="http://localhost:8000", browser_name="firefox")
    assert report.browser_name == "firefox"


def test_reporter_serializes_browser(tmp_path: Path):
    reporter = IncidentReporter(base_output_dir=str(tmp_path))
    report = IncidentReport(
        target_url="http://localhost:8000",
        persona_name="NoviceChaotic",
        browser_name="firefox",
        crashes=["ReferenceError: foo is not defined"],
    )

    run_dir = reporter.save_session(report)
    assert run_dir is not None

    incident_file = run_dir / "incident.json"
    assert incident_file.exists()

    with open(incident_file, encoding="utf-8") as f:
        data = json.load(f)

    assert data["browser"] == "firefox"
    assert data["target_url"] == "http://localhost:8000"


def test_repro_script_contains_browser_launch(tmp_path: Path):
    reporter = IncidentReporter(base_output_dir=str(tmp_path))

    for engine in ["chromium", "firefox", "webkit"]:
        report = IncidentReport(
            target_url="http://localhost:8000",
            persona_name="NoviceChaotic",
            browser_name=engine,
            crashes=["TypeError: cannot read properties of null"],
        )
        script = reporter._generate_repro_script(report)
        assert f"Browser: {engine.capitalize()}" in script
        assert f"browser = p.{engine}.launch(headless=False)" in script


def test_concurrent_repro_script_contains_browser_launch(tmp_path: Path):
    reporter = IncidentReporter(base_output_dir=str(tmp_path))

    for engine in ["chromium", "firefox", "webkit"]:
        report = IncidentReport(
            target_url="http://localhost:8000",
            concurrency=2,
            browser_name=engine,
            crashes=["HTTP 500 on /pay"],
        )
        script = reporter._generate_concurrent_repro_script(report)
        assert f"Browser: {engine.capitalize()}" in script
        assert f"browser = p.{engine}.launch(headless=False)" in script


def test_html_reporter_shows_browser_engine(tmp_path: Path):
    incident_data = {
        "run_id": "run_test_browser",
        "timestamp": "2026-10-03T12:00:00",
        "target_url": "http://localhost:8000",
        "persona": "Adversary",
        "browser": "webkit",
        "duration_seconds": 3.5,
        "actions_taken": [],
        "crashes": [],
        "http_errors": [],
        "console_errors": [],
    }

    out_file = tmp_path / "report.html"
    HTMLReporter.generate(incident_data, out_file)
    content = out_file.read_text(encoding="utf-8")

    assert "Browser Engine" in content
    assert "webkit" in content


def test_ci_step_summary_contains_browser_engine(tmp_path: Path, monkeypatch):
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    report = IncidentReport(
        target_url="http://localhost:8000",
        persona_name="Adversary",
        browser_name="firefox",
        crashes=[],
    )

    CIGate.write_github_step_summary(report, evaluations=None, run_dir=tmp_path, has_violations=False)
    assert summary_file.exists()
    content = summary_file.read_text(encoding="utf-8")

    assert "| **Browser Engine** | 🌐 `Firefox` |" in content


def test_journey_recorder_uses_specified_browser(tmp_path: Path):
    recorder = JourneyRecorder(journeys_dir=str(tmp_path))

    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.firefox.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    with patch("loki.engine.recorder.sync_playwright") as mock_sync:
        mock_sync.return_value.__enter__.return_value = mock_playwright
        recorder.record_journey("http://localhost:8000", "test_flow", browser_name="firefox")
        mock_playwright.firefox.launch.assert_called_once_with(headless=False)


def test_cli_help_shows_browser_flag():
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--browser" in result.output
    assert "-b" in result.output
    assert "chromium" in result.output
    assert "firefox" in result.output
    assert "webkit" in result.output

    record_result = runner.invoke(app, ["record", "--help"])
    assert record_result.exit_code == 0
    assert "--browser" in record_result.output
    assert "-b" in record_result.output
