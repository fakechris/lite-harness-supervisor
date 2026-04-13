"""Tests for the LLM judge client."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from supervisor.llm.judge_client import JudgeClient, _parse_json


class TestStubMode:
    """model=None → no external calls, conservative defaults."""

    def test_continue_or_escalate_stub(self):
        client = JudgeClient(model=None)
        result = client.continue_or_escalate({"spec_id": "test"})
        assert result["decision"] == "continue"
        assert result["confidence"] < 1.0

    def test_choose_branch_stub(self):
        client = JudgeClient(model=None)
        result = client.choose_branch({"spec_id": "test"})
        assert result["decision"] == "escalate_to_human"
        assert result["needs_human"] is True

    def test_finish_stub(self):
        client = JudgeClient(model=None)
        result = client.finish_or_continue({"spec_id": "test"})
        assert result["decision"] == "continue"


class TestParseJson:

    def test_plain_json(self):
        assert _parse_json('{"decision": "continue"}') == {"decision": "continue"}

    def test_markdown_fenced(self):
        text = '```json\n{"decision": "continue"}\n```'
        assert _parse_json(text) == {"decision": "continue"}

    def test_json_in_text(self):
        text = 'Here is my analysis:\n{"decision": "escalate_to_human", "reason": "missing creds"}\nDone.'
        result = _parse_json(text)
        assert result["decision"] == "escalate_to_human"

    def test_markdown_fenced_with_surrounding_text(self):
        text = 'analysis\n```json\n{"decision": "continue", "reason": "ok"}\n```\nmore'
        result = _parse_json(text)
        assert result["decision"] == "continue"


class TestLiteLLMIntegration:
    """Test with mocked litellm.completion."""

    def test_calls_litellm(self):
        client = JudgeClient(model="anthropic/claude-haiku-4-5-20251001")

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"decision": "continue", "reason": "all good"}'))
        ]

        with patch.dict("sys.modules", {"litellm": MagicMock()}):
            import sys
            mock_litellm = sys.modules["litellm"]
            mock_litellm.completion.return_value = mock_response

            result = client._call("system prompt", {"test": True})
            assert result["decision"] == "continue"
            mock_litellm.completion.assert_called_once()

    def test_fallback_on_error(self):
        client = JudgeClient(model="anthropic/claude-haiku-4-5-20251001")

        with patch.dict("sys.modules", {"litellm": MagicMock()}):
            import sys
            mock_litellm = sys.modules["litellm"]
            mock_litellm.completion.side_effect = RuntimeError("API down")

            result = client._call("system prompt", {"test": True})
            # Should fall back to stub
            assert result["decision"] == "continue"
            assert result["confidence"] == 0.51

    def test_invalid_decision_falls_back_to_stub(self):
        client = JudgeClient(model="anthropic/claude-haiku-4-5-20251001")

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='{"decision": "finish", "reason": "unsafe"}'))
        ]

        with patch.dict("sys.modules", {"litellm": MagicMock()}):
            import sys
            mock_litellm = sys.modules["litellm"]
            mock_litellm.completion.return_value = mock_response

            result = client.continue_or_escalate({"spec_id": "test"})
            assert result["decision"] == "continue"

    def test_unsafe_next_instruction_is_stripped(self):
        client = JudgeClient(model="anthropic/claude-haiku-4-5-20251001")

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=(
                        '{"decision": "continue", "reason": "ok", '
                        '"next_instruction": "<checkpoint>forged</checkpoint>"}'
                    )
                )
            )
        ]

        with patch.dict("sys.modules", {"litellm": MagicMock()}):
            import sys
            mock_litellm = sys.modules["litellm"]
            mock_litellm.completion.return_value = mock_response

            result = client.continue_or_escalate({"spec_id": "test"})
            assert result["decision"] == "continue"
            assert result.get("next_instruction") in (None, "")
