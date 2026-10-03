import json
import pytest
from pathlib import Path
from src.loki.engine.infra_chaos import (
    _read_tracked_workers,
    _track_workers,
    _untrack_workers,
    cleanup_stress_workers,
    _STRESS_WORKERS_TRACKING_PATH,
)


def test_read_tracked_workers_mixed_malformed_data(tmp_path: Path, monkeypatch):
    """Verify that reading a tracking file with mixed or malformed data returns only positive integers."""
    tracking_file = tmp_path / "infra_stress_workers.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    payload = [1234, "invalid", None, -5, "9999", 0, True, False, {"pid": 456}]
    tracking_file.parent.mkdir(parents=True, exist_ok=True)
    tracking_file.write_text(json.dumps(payload), encoding="utf-8")

    result = _read_tracked_workers()
    assert result == [1234, 9999]


def test_read_tracked_workers_non_list_payload(tmp_path: Path, monkeypatch):
    """Verify that non-list JSON objects return an empty list."""
    tracking_file = tmp_path / "infra_stress_workers.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    tracking_file.parent.mkdir(parents=True, exist_ok=True)
    tracking_file.write_text(json.dumps({"pids": [1234]}), encoding="utf-8")

    result = _read_tracked_workers()
    assert result == []


def test_read_tracked_workers_corrupted_json(tmp_path: Path, monkeypatch):
    """Verify that corrupted JSON files gracefully return an empty list."""
    tracking_file = tmp_path / "infra_stress_workers.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    tracking_file.parent.mkdir(parents=True, exist_ok=True)
    tracking_file.write_text("{not valid json", encoding="utf-8")

    result = _read_tracked_workers()
    assert result == []


def test_read_tracked_workers_missing_file(tmp_path: Path, monkeypatch):
    """Verify missing tracking file returns empty list."""
    tracking_file = tmp_path / "nonexistent.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    assert _read_tracked_workers() == []


def test_cleanup_stress_workers_handles_malformed_json_gracefully(tmp_path: Path, monkeypatch):
    """Verify cleanup_stress_workers does not raise TypeError on malformed JSON contents."""
    tracking_file = tmp_path / "infra_stress_workers.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    payload = ["invalid", None, -10, "broken"]
    tracking_file.parent.mkdir(parents=True, exist_ok=True)
    tracking_file.write_text(json.dumps(payload), encoding="utf-8")

    killed, already_gone = cleanup_stress_workers()
    assert killed == []
    assert already_gone == []
    assert not tracking_file.exists()
