import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from src.loki.ai import chat
from src.loki.engine import updater


def test_parse_version():
    assert updater.parse_version("v1.9.0") == (1, 9, 0)
    assert updater.parse_version("1.8.2") == (1, 8, 2)
    assert updater.parse_version("v2.0.0-rc1") == (2, 0, 0, 1)
    assert updater.parse_version("invalid") == (0, 0, 0)


def test_perform_update_missing_uv(monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda cmd: None)
    mock_console = MagicMock()

    success = updater.perform_update(mock_console)
    assert success is False
    assert any("not found" in str(c) for c in mock_console.print.call_args_list)


def test_perform_update_unix_success_prints_restart_notice(tmp_path: Path, monkeypatch):
    cache_file = tmp_path / ".update_cache.json"
    cache_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(updater, "CACHE_FILE", cache_file)
    monkeypatch.setattr(updater, "os", MagicMock(name="posix"))
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.setattr(updater.shutil, "which", lambda cmd: "/usr/local/bin/uv")

    mock_proc = MagicMock(returncode=0, stdout="Successfully upgraded loki-chaos-agent", stderr="")
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: mock_proc)

    mock_console = MagicMock()
    success = updater.perform_update(mock_console, exit_on_success=False)

    assert success is True
    assert not cache_file.exists()
    assert any("Restart LOKI" in str(c) for c in mock_console.print.call_args_list)


def test_perform_update_unix_exit_on_success(tmp_path: Path, monkeypatch):
    cache_file = tmp_path / ".update_cache.json"
    monkeypatch.setattr(updater, "CACHE_FILE", cache_file)
    monkeypatch.setattr(updater, "os", MagicMock(name="posix"))
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.setattr(updater.shutil, "which", lambda cmd: "/usr/local/bin/uv")

    mock_proc = MagicMock(returncode=0, stdout="Successfully upgraded loki-chaos-agent", stderr="")
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: mock_proc)

    mock_console = MagicMock()
    with pytest.raises(SystemExit) as exc_info:
        updater.perform_update(mock_console, exit_on_success=True)

    assert exc_info.value.code == 0


def test_chat_handle_update_prompts_and_exits(monkeypatch):
    session = chat.LokiChatSession()
    mock_console = MagicMock()
    session.console = mock_console

    monkeypatch.setattr(
        chat,
        "check_for_updates",
        lambda force=True: {
            "available": True,
            "current_version": "1.8.0",
            "latest_version": "1.9.0",
            "latest_tag": "v1.9.0",
            "release_url": "http://example.com",
            "uv_command": "uv tool upgrade loki-chaos-agent",
        },
    )
    monkeypatch.setattr(chat, "print_update_banner", lambda console, info: None)
    monkeypatch.setattr(chat, "perform_update", lambda console: True)

    # User confirms both update and exit
    confirm_answers = iter([True, True])
    from rich.prompt import Confirm
    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: next(confirm_answers))

    with pytest.raises(SystemExit) as exc_info:
        session._handle_update_command()

    assert exc_info.value.code == 0
    assert any("Exiting LOKI" in str(c) for c in mock_console.print.call_args_list)


def test_chat_handle_update_user_declines_exit(monkeypatch):
    session = chat.LokiChatSession()
    mock_console = MagicMock()
    session.console = mock_console

    monkeypatch.setattr(
        chat,
        "check_for_updates",
        lambda force=True: {
            "available": True,
            "current_version": "1.8.0",
            "latest_version": "1.9.0",
            "latest_tag": "v1.9.0",
            "release_url": "http://example.com",
            "uv_command": "uv tool upgrade loki-chaos-agent",
        },
    )
    monkeypatch.setattr(chat, "print_update_banner", lambda console, info: None)
    monkeypatch.setattr(chat, "perform_update", lambda console: True)

    # User confirms update, but declines exit
    confirm_answers = iter([True, False])
    from rich.prompt import Confirm
    monkeypatch.setattr(Confirm, "ask", lambda *args, **kwargs: next(confirm_answers))

    # Should not raise SystemExit
    session._handle_update_command()
    assert any("Continuing session" in str(c) for c in mock_console.print.call_args_list)
