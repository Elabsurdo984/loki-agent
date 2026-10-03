import json
from pathlib import Path
from loki.engine.html_reporter import HTMLReporter


def test_html_reporter_rule_evaluation_none_status(tmp_path: Path):
    """Test that rules_evaluations with status: None does not raise AttributeError."""
    report_data = {
        "run_id": "test_rule_none_status",
        "rules_evaluations": [
            {"rule": "Authentication required", "status": None, "observation": "No redirect observed"},
            {"rule": "Rate limit header present", "status": "PASSED", "observation": "Found 429"},
            {"rule": "No SQL errors", "status": "violated", "observation": "Syntax error leaked in 500"},
            {"rule": "Empty status field", "status": "", "observation": "Fallback to unknown"},
        ],
    }
    out_html = tmp_path / "report_rule_none.html"
    result = HTMLReporter.generate(report_data, out_html)
    assert result.exists()
    content = out_html.read_text(encoding="utf-8")
    assert "UNKNOWN" in content
    assert "Authentication required" in content
    assert "VIOLATED" in content
    assert "FAIL / ISSUES DETECTED" in content


def test_html_reporter_concurrency_lanes_none_status(tmp_path: Path):
    """Test that concurrency_lanes with response status: None does not raise TypeError."""
    report_data = {
        "run_id": "test_concurrency_none_status",
        "concurrency": 2,
        "concurrency_lanes": [
            {
                "lane": 1,
                "selector": "#submit-btn",
                "responses": [{"method": "POST", "status": None}],
                "crashes": [],
            },
            {
                "lane": 2,
                "selector": "#submit-btn",
                "responses": [{"method": "POST", "status": "200"}],
                "crashes": [],
            },
        ],
    }
    out_html = tmp_path / "report_concurrency_none.html"
    result = HTMLReporter.generate(report_data, out_html)
    assert result.exists()
    content = out_html.read_text(encoding="utf-8")
    assert "Concurrency Probe" in content
    assert "Lane 1" in content
    assert "Lane 2" in content


def test_html_reporter_api_faults_string_and_none_status(tmp_path: Path):
    """Test that api_faults with string or None statuses do not raise TypeError."""
    report_data = {
        "run_id": "test_api_faults_status_casting",
        "api_faults": [
            {
                "url": "http://example.com/api/test1",
                "method": "GET",
                "fault_type": "chaos_mutation",
                "injected_status": "CORRUPTED",
            },
            {
                "url": "http://example.com/api/test2",
                "method": "POST",
                "fault_type": "chaos_500",
                "injected_status": "500",
            },
            {
                "url": "http://example.com/api/test3",
                "method": "PUT",
                "fault_type": "chaos_null",
                "injected_status": None,
                "status": None,
            },
            {
                "url": "http://example.com/api/test4",
                "method": "DELETE",
                "fault_type": "chaos_404_str",
                "injected_status": "404",
            },
            {
                "url": "http://example.com/api/test5",
                "method": "GET",
                "fault_type": "chaos_200_str",
                "injected_status": "200",
            },
        ],
    }
    out_html = tmp_path / "report_api_faults.html"
    result = HTMLReporter.generate(report_data, out_html)
    assert result.exists()
    content = out_html.read_text(encoding="utf-8")
    assert "CORRUPTED" in content
    assert "500" in content
    assert "Mutated" in content
    assert "404" in content
    assert "200" in content


def test_html_reporter_har_entries_status_coalescing(tmp_path: Path):
    """Test that HAR log entries with None or string status codes are rendered safely."""
    har_file = tmp_path / "network.har"
    har_payload = {
        "log": {
            "entries": [
                {
                    "request": {"method": "GET", "url": "https://example.com/api/none"},
                    "response": {"status": None},
                    "time": 42.5,
                },
                {
                    "request": {"method": "POST", "url": "https://example.com/api/str200"},
                    "response": {"status": "200"},
                    "time": 10.0,
                },
                {
                    "request": {"method": "GET", "url": "https://example.com/api/str500"},
                    "response": {"status": "500"},
                    "time": 120.0,
                },
                {
                    "request": {"method": "DELETE", "url": "https://example.com/api/corrupted"},
                    "response": {"status": "INVALID"},
                    "time": None,
                },
            ]
        }
    }
    har_file.write_text(json.dumps(har_payload), encoding="utf-8")

    report_data = {
        "run_id": "test_har_status_coalescing",
        "har_file": har_file.name,
    }
    out_html = tmp_path / "report_har.html"
    result = HTMLReporter.generate(report_data, out_html)
    assert result.exists()
    content = out_html.read_text(encoding="utf-8")
    assert "Sanitized Network Archive" in content
    assert "https://example.com/api/none" in content
    assert "https://example.com/api/str200" in content
    assert "https://example.com/api/str500" in content
    assert "https://example.com/api/corrupted" in content


def test_html_reporter_full_synthetic_payload_with_all_none_and_edge_values(tmp_path: Path):
    """Test full incident report generation with None duration, None concurrency, and edge inputs."""
    report_data = {
        "run_id": None,
        "target_url": None,
        "timestamp": None,
        "duration_seconds": None,
        "persona": None,
        "crashes": None,
        "http_errors": None,
        "actions_taken": None,
        "rules_evaluations": None,
        "layout_issues": None,
        "concurrency": None,
        "concurrency_lanes": None,
        "api_faults": None,
    }
    out_html = tmp_path / "report_edge_payload.html"
    result = HTMLReporter.generate(report_data, out_html)
    assert result.exists()
    content = out_html.read_text(encoding="utf-8")
    assert "0.00s" in content
    assert "ALL CHECKS PASSED" in content


def test_html_reporter_safe_int_helper():
    """Verify HTMLReporter._safe_int handles varied input types hermetically."""
    assert HTMLReporter._safe_int(200) == 200
    assert HTMLReporter._safe_int("200") == 200
    assert HTMLReporter._safe_int(None, 0) == 0
    assert HTMLReporter._safe_int(None, 42) == 42
    assert HTMLReporter._safe_int("INVALID", 400) == 400
    assert HTMLReporter._safe_int({}, 500) == 500
    assert HTMLReporter._safe_int([], 1) == 1
    assert HTMLReporter._safe_int(3.14) == 3
