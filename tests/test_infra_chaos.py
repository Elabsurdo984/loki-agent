import json
from pathlib import Path
from src.loki.engine.infra_chaos import (
    _read_tracked_workers,
    _track_workers,
    _untrack_workers,
    cleanup_stress_workers,
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


def test_concurrent_track_workers_threads(tmp_path: Path, monkeypatch):
    """Verify that multiple concurrent threads tracking PIDs do not lose updates."""
    import concurrent.futures

    tracking_file = tmp_path / "infra_stress_workers.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    pids_to_track = [2000 + i for i in range(20)]

    def worker_track(pid):
        _track_workers([pid])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(worker_track, pids_to_track))

    tracked = _read_tracked_workers()
    assert sorted(tracked) == sorted(pids_to_track)


def test_concurrent_track_and_untrack_workers(tmp_path: Path, monkeypatch):
    """Verify concurrent track and untrack operations maintain consistency."""
    import concurrent.futures

    tracking_file = tmp_path / "infra_stress_workers.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    initial_pids = [3000 + i for i in range(10)]
    _track_workers(initial_pids)

    def task(i):
        if i % 2 == 0:
            # Add new PID
            _track_workers([4000 + i])
        else:
            # Untrack initial PID
            _untrack_workers([3000 + i])

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(task, range(10)))

    tracked = _read_tracked_workers()
    # Even initial PIDs (3000, 3002, 3004, 3006, 3008) should remain
    # Even new PIDs (4000, 4002, 4004, 4006, 4008) should be added
    expected = [3000 + i for i in range(10) if i % 2 == 0] + [4000 + i for i in range(10) if i % 2 == 0]
    assert sorted(tracked) == sorted(expected)


def test_track_workers_empty_list_noop(tmp_path: Path, monkeypatch):
    """Verify tracking empty PID list is a no-op."""
    tracking_file = tmp_path / "infra_stress_workers.json"
    monkeypatch.setattr("src.loki.engine.infra_chaos._STRESS_WORKERS_TRACKING_PATH", tracking_file)

    _track_workers([])
    assert not tracking_file.exists()
    assert _read_tracked_workers() == []


def test_kill_process_partial_failure_does_not_mask_killed(monkeypatch):
    """Verify that if one process fails during kill, previously killed processes are reported and not masked."""
    from unittest.mock import MagicMock
    import psutil
    from src.loki.engine import infra_chaos

    p1 = MagicMock(pid=1001)
    p1.name.return_value = "worker1.exe"
    p1.kill.return_value = None

    p2 = MagicMock(pid=1002)
    p2.name.return_value = "worker2.exe"
    p2.kill.side_effect = psutil.AccessDenied()

    monkeypatch.setattr(infra_chaos, "find_processes", lambda **kwargs: [p1, p2])
    monkeypatch.setattr(infra_chaos, "_is_protected", lambda proc: False)

    result = infra_chaos.kill_process(port=8080)
    assert result.success is True
    assert result.pid == 1001
    assert any("worker1.exe (pid 1001)" in detail for detail in [result.detail])
    assert any("failed to kill" in detail for detail in [result.detail])
    assert result.extra["killed"] == [(1001, "worker1.exe")]
    assert len(result.extra["failed"]) == 1
    assert result.extra["failed"][0][0] == 1002


def test_kill_process_all_failed(monkeypatch):
    """Verify kill_process returns success=False when all targets fail."""
    from unittest.mock import MagicMock
    import psutil
    from src.loki.engine import infra_chaos

    p1 = MagicMock(pid=1001)
    p1.name.return_value = "worker1.exe"
    p1.kill.side_effect = psutil.AccessDenied()

    monkeypatch.setattr(infra_chaos, "find_processes", lambda **kwargs: [p1])
    monkeypatch.setattr(infra_chaos, "_is_protected", lambda proc: False)

    result = infra_chaos.kill_process(port=8080)
    assert result.success is False
    assert result.extra["killed"] == []
    assert len(result.extra["failed"]) == 1


def test_pause_process_pauses_all_matching_processes(monkeypatch):
    """Verify pause_process suspends and resumes ALL matching processes on shared port."""
    from unittest.mock import MagicMock
    from src.loki.engine import infra_chaos

    p1 = MagicMock(pid=2001)
    p1.name.return_value = "worker1.exe"
    p2 = MagicMock(pid=2002)
    p2.name.return_value = "worker2.exe"

    monkeypatch.setattr(infra_chaos, "find_processes", lambda **kwargs: [p1, p2])
    monkeypatch.setattr(infra_chaos, "_is_protected", lambda proc: False)

    result = infra_chaos.pause_process(port=8080, duration=0.01)
    assert result.success is True
    p1.suspend.assert_called_once()
    p2.suspend.assert_called_once()
    p1.resume.assert_called_once()
    p2.resume.assert_called_once()
    assert result.extra["suspended"] == [2001, 2002]
    assert len(result.extra["resumed"]) == 2


def test_pause_process_partial_suspend_failure(monkeypatch):
    """Verify pause_process resumes successful suspends even if another process fails to suspend."""
    from unittest.mock import MagicMock
    import psutil
    from src.loki.engine import infra_chaos

    p1 = MagicMock(pid=2001)
    p1.name.return_value = "worker1.exe"
    p2 = MagicMock(pid=2002)
    p2.name.return_value = "worker2.exe"
    p2.suspend.side_effect = psutil.AccessDenied()

    monkeypatch.setattr(infra_chaos, "find_processes", lambda **kwargs: [p1, p2])
    monkeypatch.setattr(infra_chaos, "_is_protected", lambda proc: False)

    result = infra_chaos.pause_process(port=8080, duration=0.01)
    assert result.success is True
    p1.suspend.assert_called_once()
    p1.resume.assert_called_once()
    p2.suspend.assert_called_once()
    p2.resume.assert_not_called()
    assert result.extra["suspended"] == [2001]
    assert len(result.extra["resumed"]) == 1
    assert len(result.extra["failed_suspend"]) == 1


