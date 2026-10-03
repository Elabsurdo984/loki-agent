from loki.ai import chat


def test_failed_chat_completion_does_not_keep_orphan_user_turn(monkeypatch):
    prompts = iter(["first question", "second question", "exit"])
    requests = []

    monkeypatch.setattr(chat.LokiChatSession, "_read_input", lambda self: next(prompts))
    monkeypatch.setattr(chat.LokiChatSession, "_load_project_context", lambda self: "")
    monkeypatch.setattr(chat, "check_for_updates", lambda: None)
    monkeypatch.setattr(chat, "is_ai_customized", lambda model: False)
    monkeypatch.setattr(chat, "resolve_ai_connection", lambda model: {"model": "test-provider"})

    def fail_completion(**kwargs):
        requests.append([dict(message) for message in kwargs["messages"]])
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(chat.litellm, "completion", fail_completion)

    session = chat.LokiChatSession()
    session.start()

    assert len(requests) == 8
    for request in requests[:4]:
        assert [message["role"] for message in request] == ["system", "user"]
        assert request[-1]["content"] == "first question"
    for request in requests[4:]:
        assert [message["role"] for message in request] == ["system", "user"]
        assert request[-1]["content"] == "second question"
    assert len(session.history) == 1
    assert session.history[0]["role"] == "system"


def test_run_cli_action_catches_system_exit(monkeypatch):
    import sys
    from unittest.mock import MagicMock
    import typer

    session = chat.LokiChatSession()
    mock_console = MagicMock()
    session.console = mock_console

    # 1. SystemExit with int code
    def exit_with_code():
        sys.exit(1)

    session._run_cli_action("test action", exit_with_code)
    assert any("finished with exit code 1" in str(call) for call in mock_console.print.call_args_list)

    # 2. SystemExit with string message
    mock_console.reset_mock()

    def exit_with_msg():
        sys.exit("fatal failure")

    session._run_cli_action("test action", exit_with_msg)
    assert any("fatal failure" in str(call) for call in mock_console.print.call_args_list)

    # 3. SystemExit(0) should be quiet / no error
    mock_console.reset_mock()

    def exit_with_zero():
        sys.exit(0)

    session._run_cli_action("test action", exit_with_zero)
    assert not any("Error" in str(call) for call in mock_console.print.call_args_list)

    # 4. typer.Exit(1)
    mock_console.reset_mock()

    def exit_with_typer():
        raise typer.Exit(1)

    session._run_cli_action("test action", exit_with_typer)
    assert any("finished with exit code 1" in str(call) for call in mock_console.print.call_args_list)


def test_chat_repl_survives_cli_action_system_exit(monkeypatch):
    import sys

    # Simulate user running a command that calls sys.exit(1), followed by exit
    prompts = iter(["/run http://example.com --ci", "exit"])
    monkeypatch.setattr(chat.LokiChatSession, "_read_input", lambda self: next(prompts))
    monkeypatch.setattr(chat.LokiChatSession, "_load_project_context", lambda self: "")
    monkeypatch.setattr(chat, "check_for_updates", lambda: None)

    # Mock the underlying run function to call sys.exit(1)
    from loki import cli

    def crashing_run(**kwargs):
        sys.exit(1)

    monkeypatch.setattr(cli, "run", crashing_run)

    session = chat.LokiChatSession()
    # If SystemExit escaped, this would raise SystemExit and fail the test
    session.start()
    # Reached here cleanly!

