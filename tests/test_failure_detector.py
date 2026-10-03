import json
from pathlib import Path
from unittest.mock import MagicMock
from loki.engine.sandbox import IncidentReport, ChaosSandbox
from loki.engine.reporter import IncidentReporter
from loki.engine.html_reporter import HTMLReporter
from loki.engine.ci import CIGate
from loki.engine.healer import CodeHealer


def test_incident_report_has_crashes_flags_all_failure_vectors():
    # 1. Base clean report
    report = IncidentReport(target_url="http://localhost:8000")
    assert not report.has_crashes
    assert not report.has_failures
    assert report.total_failures_count == 0

    # 2. Console error (silent break)
    report.console_errors.append("Uncaught TypeError: Cannot read properties of undefined")
    assert report.has_crashes
    assert report.has_failures
    assert report.total_failures_count == 1

    # 3. HTTP 4xx/5xx
    report.http_errors.append("HTTP 404 on /api/user")
    assert report.total_failures_count == 2

    # 4. Failed request / CORS
    report.failed_requests.append("[GET] /api/checkout (xhr) - net::ERR_FAILED (CORS)")
    assert report.total_failures_count == 3

    # 5. Unhandled Promise Rejection
    report.unhandled_rejections.append("Unhandled Promise Rejection: Network request timed out")
    assert report.total_failures_count == 4

    # 6. Navigation error / error screen
    report.navigation_errors.append("Navigated to browser error page: chrome-error://chromewebdata/")
    assert report.total_failures_count == 5

    # 7. Resource failure
    report.resource_failures.append("Failed to load <IMG> resource: http://localhost:8000/missing.png")
    assert report.total_failures_count == 6

    # 8. Unexpected dialog
    report.unexpected_dialogs.append("Unexpected alert dialog: 'Session expired'")
    assert report.total_failures_count == 7

    # 9. Traditional crash
    report.crashes.append("Page crashed")
    assert report.total_failures_count == 8


def test_sniff_error_page_detection():
    sandbox = ChaosSandbox()

    mock_page = MagicMock()
    mock_page.is_closed.return_value = False

    # Title detection
    mock_page.title.return_value = "500 Internal Server Error"
    mock_page.url = "http://localhost:8000/dashboard"
    result = sandbox._sniff_error_page(mock_page)
    assert result is not None
    assert "Application error page detected by title" in result

    # 404 title
    mock_page.title.return_value = "Page Not Found | MyApp"
    result = sandbox._sniff_error_page(mock_page)
    assert result is not None
    assert "Application error page detected by title" in result

    # URL path detection
    mock_page.title.return_value = "Welcome"
    mock_page.url = "http://localhost:8000/crash"
    result = sandbox._sniff_error_page(mock_page)
    assert result is not None
    assert "Application navigated to error route" in result

    # Clean page
    mock_page.title.return_value = "Home Dashboard"
    mock_page.url = "http://localhost:8000/home"
    assert sandbox._sniff_error_page(mock_page) is None


def test_sniff_broken_resources_mock():
    sandbox = ChaosSandbox()
    mock_page = MagicMock()
    mock_page.is_closed.return_value = False
    mock_page.evaluate.return_value = ["http://localhost:8000/broken.jpg"]

    issues = sandbox._sniff_broken_resources(mock_page)
    assert len(issues) == 1
    assert "Broken image in DOM (0px natural width): http://localhost:8000/broken.jpg" in issues[0]


def test_reporter_persists_all_failure_vectors(tmp_path: Path):
    reporter = IncidentReporter(base_output_dir=str(tmp_path))
    report = IncidentReport(
        target_url="http://localhost:8000",
        persona_name="Novice Chaotic",
        console_errors=["Console error: ReferenceError: x is not defined"],
        unhandled_rejections=["Unhandled Promise Rejection: TypeError: fetch failed"],
        failed_requests=["[GET] http://localhost:8000/api/missing (xhr) - net::ERR_CONNECTION_REFUSED"],
        unexpected_dialogs=["Unexpected alert dialog: 'Warning'"],
        resource_failures=["Failed to load <SCRIPT> resource: http://localhost:8000/bundle.js"],
        navigation_errors=["Application navigated to error route: '/error' (http://localhost:8000/error)"],
    )

    run_dir = reporter.save_session(report)
    incident_file = run_dir / "incident.json"
    assert incident_file.exists()

    data = json.loads(incident_file.read_text(encoding="utf-8"))
    assert len(data["console_errors"]) == 1
    assert len(data["unhandled_rejections"]) == 1
    assert len(data["failed_requests"]) == 1
    assert len(data["unexpected_dialogs"]) == 1
    assert len(data["resource_failures"]) == 1
    assert len(data["navigation_errors"]) == 1
    assert data["total_failures_count"] == 6

    # Repro script should be generated since report.has_crashes is True
    repro_file = run_dir / "repro_test.py"
    assert repro_file.exists()
    repro_content = repro_file.read_text(encoding="utf-8")
    assert 'page.on("console"' in repro_content
    assert 'page.on("requestfailed"' in repro_content
    assert 'page.on("dialog"' in repro_content


def test_html_reporter_renders_silent_console_errors_and_anomalies(tmp_path: Path):
    html_file = tmp_path / "report.html"
    metadata = {
        "run_id": "test_run_1",
        "timestamp": "2026-10-03T12:00:00",
        "target_url": "http://localhost:8000",
        "persona": "Adversary",
        "crashes": [],
        "http_errors": ["HTTP 404 on /api/data"],
        "console_errors": ["Uncaught TypeError in main.js"],
        "failed_requests": ["[GET] http://localhost:8000/api/dead (fetch) - net::ERR_FAILED"],
        "unhandled_rejections": ["Unhandled Promise Rejection: Auth token expired"],
        "navigation_errors": ["Application error page detected by title: '500 Server Error'"],
        "resource_failures": ["Broken image in DOM (0px natural width): http://localhost:8000/logo.png"],
        "unexpected_dialogs": ["Unexpected confirm dialog: 'Are you sure?'"],
        "actions_taken": ["Clicked #button"],
    }

    HTMLReporter.generate(metadata, html_file)
    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")

    # Verdict must be FAIL
    assert "FAIL / ISSUES DETECTED" in content
    assert "Failures Detected" in content
    assert "Console Error (Silent Break):" in content
    assert "Unhandled Promise Rejection:" in content
    assert "Request Failed / CORS:" in content
    assert "Navigation / Error Page:" in content
    assert "Broken Resource:" in content
    assert "Unexpected Dialog:" in content


def test_ci_gate_step_summary_includes_all_failure_details(tmp_path: Path, monkeypatch):
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    report = IncidentReport(
        target_url="http://localhost:8000",
        crashes=["Uncaught Error: Crash"],
        console_errors=["Console error: Silent bug"],
        http_errors=["HTTP 400 Bad Request"],
        failed_requests=["[POST] /api/save - net::ERR_ABORTED"],
        unhandled_rejections=["Unhandled Promise Rejection: Timeout"],
        resource_failures=["Failed to load <IMG> http://localhost:8000/pic.jpg"],
        unexpected_dialogs=["Unexpected alert dialog: 'Halt'"],
        navigation_errors=["Application navigated to error route: '/crash'"],
    )

    CIGate.write_github_step_summary(
        report=report,
        evaluations=None,
        run_dir=tmp_path,
        has_violations=False,
    )

    assert summary_file.exists()
    text = summary_file.read_text(encoding="utf-8")
    assert "**Failures Detected** | `8`" in text
    assert "**Console Error:** `Console error: Silent bug`" in text
    assert "**Unhandled Rejection:** `Unhandled Promise Rejection: Timeout`" in text
    assert "**Request Failed / CORS:** `[POST] /api/save - net::ERR_ABORTED`" in text
    assert "**Broken Resource:** `Failed to load <IMG> http://localhost:8000/pic.jpg`" in text
    assert "**Unexpected Dialog:** `Unexpected alert dialog: 'Halt'`" in text


def test_code_healer_inspects_console_and_rejection_logs(tmp_path: Path):
    healer = CodeHealer(runs_dir=str(tmp_path))

    # Create dummy app file
    app_file = tmp_path / "broken_app.js"
    app_file.write_text("function test() { console.log('hello'); }", encoding="utf-8")

    # Incident with only console error referencing broken_app.js
    incident_data = {
        "crashes": [],
        "console_errors": [f"Error in {app_file.name}: undefined is not a function"],
        "unhandled_rejections": [],
    }

    import os
    orig_cwd = os.getcwd()
    try:
        os.chdir(str(tmp_path))
        resolved = healer.resolve_source_file(incident_data)
        assert resolved is not None
        assert resolved.name == "broken_app.js"
    finally:
        os.chdir(orig_cwd)
