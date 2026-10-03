from unittest.mock import MagicMock
import pytest

from src.loki.ai.chat import LokiChatSession


class TestChatSessionHistory:
    def test_orphan_user_message_removed_on_model_failure(self, monkeypatch):
        session = LokiChatSession()

        # Mock inputs: first query fails, second command exits
        user_inputs = iter(["What caused the crash?", "exit"])
        monkeypatch.setattr(session, "_read_input", lambda: next(user_inputs))

        # Mock updater to avoid network requests
        monkeypatch.setattr("src.loki.ai.chat.check_for_updates", lambda: None)

        # Mock litellm.completion to fail with service unavailable
        monkeypatch.setattr(
            "src.loki.ai.chat.litellm.completion",
            MagicMock(side_effect=RuntimeError("503 Service Unavailable")),
        )

        session.start()

        # The orphan user turn must be popped so history retains only system instruction
        roles = [msg["role"] for msg in session.history]
        assert roles == ["system"]

    def test_orphan_user_message_removed_on_reported_error(self, monkeypatch):
        session = LokiChatSession()

        user_inputs = iter(["Diagnose error", "exit"])
        monkeypatch.setattr(session, "_read_input", lambda: next(user_inputs))
        monkeypatch.setattr("src.loki.ai.chat.check_for_updates", lambda: None)

        # Mock litellm.completion to fail with an unhandled/reported error
        monkeypatch.setattr(
            "src.loki.ai.chat.litellm.completion",
            MagicMock(side_effect=RuntimeError("400 Bad Request")),
        )

        session.start()

        roles = [msg["role"] for msg in session.history]
        assert roles == ["system"]

    def test_role_alternation_preserved_after_transient_failure(self, monkeypatch):
        session = LokiChatSession()

        user_inputs = iter(["Failing query", "Successful query", "exit"])
        monkeypatch.setattr(session, "_read_input", lambda: next(user_inputs))
        monkeypatch.setattr("src.loki.ai.chat.check_for_updates", lambda: None)

        # Mock stream response helper for the successful query
        def make_stream_chunk(text):
            choice = MagicMock()
            choice.delta.content = text
            chunk = MagicMock()
            chunk.choices = [choice]
            return chunk

        def mock_completion(*args, **kwargs):
            messages = kwargs.get("messages") or []
            if any("Failing query" in m.get("content", "") for m in messages):
                raise RuntimeError("503 Unavailable")

            def stream_gen():
                yield make_stream_chunk("Diagnostic ")
                yield make_stream_chunk("analysis complete.")

            return stream_gen()

        monkeypatch.setattr("src.loki.ai.chat.litellm.completion", mock_completion)

        session.start()

        roles = [msg["role"] for msg in session.history]
        assert roles == ["system", "user", "assistant"]
        assert session.history[1]["content"] == "Successful query"
        assert session.history[2]["content"] == "Diagnostic analysis complete."
