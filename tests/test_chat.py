from src.loki.ai import chat


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
