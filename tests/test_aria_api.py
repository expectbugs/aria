"""Tests for aria_api.py — Anthropic API client, tool handlers, tool call loop.

SAFETY: All API calls and database access mocked. No real Anthropic API calls.
"""

from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock
from types import SimpleNamespace

import pytest

import aria_api


# --- Helper: mock Anthropic response objects ---

def _make_text_block(text):
    return SimpleNamespace(type="text", text=text)


def _make_thinking_block(thinking):
    return SimpleNamespace(type="thinking", thinking=thinking)


def _make_tool_use_block(tool_id, name, input_data):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=input_data)


def _make_response(content_blocks, stop_reason="end_turn"):
    usage = SimpleNamespace(
        input_tokens=100, output_tokens=50,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return SimpleNamespace(content=content_blocks, stop_reason=stop_reason,
                           usage=usage)


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the API client singleton before each test."""
    aria_api._client = None
    yield
    aria_api._client = None


class TestGetClient:

    @patch("aria_api.Path")
    def test_creates_client_from_file(self, mock_path):
        mock_path.return_value.exists.return_value = True
        mock_path.return_value.read_text.return_value = "sk-ant-test-key\n"

        with patch("aria_api.anthropic.Anthropic") as mock_anthropic:
            client = aria_api._get_client()
            mock_anthropic.assert_called_once_with(
                api_key="sk-ant-test-key", timeout=600.0
            )

    @patch("aria_api.Path")
    def test_raises_without_key(self, mock_path):
        mock_path.return_value.exists.return_value = False
        with patch("aria_api.config") as mock_cfg:
            from pathlib import Path as RealPath
            mock_cfg.DATA_DIR = RealPath("/tmp")
            mock_cfg.ANTHROPIC_API_KEY_FILE = RealPath("/nonexistent/key.txt")
            mock_cfg.ANTHROPIC_API_KEY = ""
            with pytest.raises(RuntimeError, match="No Anthropic API key"):
                aria_api._get_client()


class TestToolHandlers:
    """Test each tool handler in isolation."""

    @patch("aria_api.health_store")
    def test_query_health_log(self, mock_hs):
        mock_hs.get_entries.return_value = [
            {"id": "h1", "date": "2026-03-17", "category": "pain",
             "description": "back pain", "severity": 8, "sleep_hours": None,
             "meal_type": None},
        ]
        result = aria_api._handle_tool_call("query_health_log", {"days": 7})
        assert "back pain" in result
        assert "severity 8" in result
        assert "h1" in result

    @patch("aria_api.health_store")
    def test_query_health_log_empty(self, mock_hs):
        mock_hs.get_entries.return_value = []
        result = aria_api._handle_tool_call("query_health_log", {"days": 30})
        assert "No health entries" in result

    @patch("aria_api.nutrition_store")
    def test_query_nutrition_log(self, mock_ns):
        mock_ns.get_items.return_value = [
            {"id": "n1", "time": "12:30", "meal_type": "lunch",
             "food_name": "Chicken", "servings": 1, "nutrients": {"calories": 450}},
        ]
        mock_ns.get_daily_totals.return_value = {
            "item_count": 1, "calories": 450, "protein_g": 38,
            "dietary_fiber_g": 5, "added_sugars_g": 2, "sodium_mg": 680,
            "omega3_mg": 0,
        }
        result = aria_api._handle_tool_call("query_nutrition_log", {"date": "2026-03-19"})
        assert "Chicken" in result
        assert "450" in result
        assert "38" in result

    @patch("aria_api.vehicle_store")
    def test_query_vehicle_log(self, mock_vs):
        mock_vs.get_entries.return_value = [
            {"id": "v1", "date": "2026-03-10", "event_type": "oil_change",
             "description": "Full synthetic", "mileage": 146200, "cost": 52.99},
        ]
        mock_vs.get_latest_by_type.return_value = {"oil_change": {"date": "2026-03-10", "mileage": 146200}}
        result = aria_api._handle_tool_call("query_vehicle_log", {})
        assert "oil_change" in result
        assert "146200" in result
        assert "$52.99" in result

    @patch("aria_api.legal_store")
    def test_query_legal_log(self, mock_ls):
        mock_ls.get_entries.return_value = [
            {"id": "l1", "date": "2026-03-05", "entry_type": "court_date",
             "description": "Initial hearing"},
        ]
        mock_ls.get_upcoming_dates.return_value = [
            {"date": "2026-04-01", "description": "Motion hearing"},
        ]
        result = aria_api._handle_tool_call("query_legal_log", {})
        assert "Initial hearing" in result
        assert "Motion hearing" in result

    @patch("aria_api.calendar_store")
    def test_query_calendar(self, mock_cal):
        mock_cal.get_events.return_value = [
            {"id": "e1", "date": "2026-03-20", "title": "Dentist", "time": "14:30"},
        ]
        result = aria_api._handle_tool_call("query_calendar",
                                             {"start_date": "2026-03-20", "end_date": "2026-03-27"})
        assert "Dentist" in result
        assert "14:30" in result

    @patch("aria_api.db.get_conn")
    def test_query_conversations(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = [
            {"timestamp": "2026-03-20T14:00:00", "input": "How are you?",
             "response": "Doing great!"},
        ]
        result = aria_api._handle_tool_call("query_conversations",
                                             {"days": 7, "search_text": "great"})
        assert "How are you?" in result
        assert "Doing great!" in result

    def test_unknown_tool(self):
        result = aria_api._handle_tool_call("nonexistent_tool", {})
        assert "Unknown tool" in result

    @patch("aria_api.health_store")
    def test_tool_error_handling(self, mock_hs):
        mock_hs.get_entries.side_effect = Exception("DB connection lost")
        result = aria_api._handle_tool_call("query_health_log", {"days": 7})
        assert "Error executing" in result


class TestAskAria:
    """Test the main query function with mocked API."""

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_basic_query(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("Hello Adam!")], stop_reason="end_turn"
        )

        result = await aria_api.ask_aria("Hey there")
        assert result == "Hello Adam!"
        mock_api.messages.create.assert_called_once()

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_context_injected_in_system(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("Got it")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("test", extra_context="Weather: Sunny 55F")

        call_kwargs = mock_api.messages.create.call_args[1]
        system_blocks = call_kwargs["system"]
        assert isinstance(system_blocks, list)
        assert len(system_blocks) == 2  # static + context
        assert any("Weather: Sunny 55F" in b["text"] for b in system_blocks)
        assert any("[CONTEXT]" in b["text"] for b in system_blocks)
        assert system_blocks[0].get("cache_control") == {"type": "ephemeral"}

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns")
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_history_included(self, mock_prompt, mock_history, mock_client):
        mock_history.return_value = [
            {"role": "user", "content": "Previous question"},
            {"role": "assistant", "content": "Previous answer"},
        ]
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("Response")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("New question")

        call_kwargs = mock_api.messages.create.call_args[1]
        messages = call_kwargs["messages"]
        assert len(messages) == 3  # 2 history + 1 current
        assert messages[0]["content"] == "Previous question"
        assert messages[2]["content"] == "New question"

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_file_blocks_multimodal(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("I see an image")], stop_reason="end_turn"
        )

        blocks = [{"type": "image", "source": {"type": "base64",
                   "media_type": "image/jpeg", "data": "base64data"}}]
        await aria_api.ask_aria("What is this?", file_blocks=blocks)

        call_kwargs = mock_api.messages.create.call_args[1]
        messages = call_kwargs["messages"]
        last_msg = messages[-1]
        assert isinstance(last_msg["content"], list)
        assert any(b.get("type") == "image" for b in last_msg["content"])

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    @patch("aria_api._handle_tool_call", return_value="Tool result data")
    async def test_tool_call_loop(self, mock_tool, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api

        # First call returns tool_use, second returns final text
        mock_api.messages.create.side_effect = [
            _make_response(
                [_make_tool_use_block("tc1", "query_health_log", {"days": 7})],
                stop_reason="tool_use"
            ),
            _make_response(
                [_make_text_block("Based on your health log, here's what I found...")],
                stop_reason="end_turn"
            ),
        ]

        result = await aria_api.ask_aria("What happened with my back pain?")
        assert "here's what I found" in result
        mock_tool.assert_called_once_with("query_health_log", {"days": 7})
        assert mock_api.messages.create.call_count == 2

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_thinking_blocks_filtered(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_thinking_block("Let me think..."), _make_text_block("Here's my answer")],
            stop_reason="end_turn"
        )

        result = await aria_api.ask_aria("Complex question")
        assert result == "Here's my answer"
        assert "Let me think" not in result

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_action_blocks_in_response(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        response_text = 'Logged your meal! <!--ACTION::{"action": "log_health", "date": "2026-03-25", "category": "meal", "description": "chicken", "meal_type": "lunch"}-->'
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block(response_text)], stop_reason="end_turn"
        )

        result = await aria_api.ask_aria("I had chicken for lunch")
        assert "ACTION" in result
        assert "log_health" in result

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_api_timeout_raises(self, mock_prompt, mock_history, mock_client):
        import anthropic
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())

        with pytest.raises(RuntimeError, match="timed out"):
            await aria_api.ask_aria("Hello")

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_tools_passed_to_api(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("OK")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("Hello")

        call_kwargs = mock_api.messages.create.call_args[1]
        assert "tools" in call_kwargs
        tool_names = [t["name"] for t in call_kwargs["tools"]]
        assert "query_health_log" in tool_names
        assert "query_nutrition_log" in tool_names
        assert "query_vehicle_log" in tool_names
        assert "query_legal_log" in tool_names
        assert "query_calendar" in tool_names
        assert "query_conversations" in tool_names


class TestPromptCaching:
    """Verify prompt caching structure."""

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_system_is_cached_blocks(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("OK")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("test query", extra_context="some context")

        call_kwargs = mock_api.messages.create.call_args[1]
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == "System prompt"

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_no_context_single_block(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("OK")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("test query")

        call_kwargs = mock_api.messages.create.call_args[1]
        system = call_kwargs["system"]
        assert len(system) == 1  # no context block

    def test_cached_tools_have_cache_control(self):
        assert "cache_control" in aria_api.CACHED_TOOLS[-1]
        assert aria_api.CACHED_TOOLS[-1]["cache_control"] == {"type": "ephemeral"}
        # Other tools should NOT have cache_control
        for tool in aria_api.CACHED_TOOLS[:-1]:
            assert "cache_control" not in tool

    def test_cached_tools_preserve_originals(self):
        """CACHED_TOOLS should not mutate the original TOOLS list."""
        assert "cache_control" not in aria_api.TOOLS[-1]


class TestSimpleQueryDetection:
    """Test the _is_simple_query() function."""

    def test_exact_timer(self):
        assert aria_api._is_simple_query("set a timer for 30 minutes")

    def test_exact_reminder(self):
        assert aria_api._is_simple_query("Set a reminder to call mom")

    def test_cancel_timer(self):
        assert aria_api._is_simple_query("cancel the timer")

    def test_remove_reminder(self):
        assert aria_api._is_simple_query("remove the reminder")

    def test_delete_appointment(self):
        assert aria_api._is_simple_query("delete the appointment")

    def test_weather(self):
        assert aria_api._is_simple_query("What is the weather")

    def test_greeting_exact(self):
        assert aria_api._is_simple_query("hello")

    def test_greeting_with_text_not_simple(self):
        assert not aria_api._is_simple_query("hello I have a complex question about my diet")

    def test_complex_query(self):
        assert not aria_api._is_simple_query(
            "what should I eat for dinner given my nutritional profile")

    def test_health_query(self):
        assert not aria_api._is_simple_query("how is my health looking")

    def test_thanks_with_punctuation(self):
        assert aria_api._is_simple_query("thanks!")

    def test_good_morning_not_simple(self):
        # good morning needs full thinking for briefing
        assert not aria_api._is_simple_query("good morning")

    def test_good_night_not_simple(self):
        assert not aria_api._is_simple_query("good night")

    def test_good_afternoon_exact_is_simple(self):
        assert aria_api._is_simple_query("good afternoon")

    def test_good_afternoon_with_text_not_simple(self):
        assert not aria_api._is_simple_query("good afternoon can you look into my diet")

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_simple_query_skips_thinking(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("Timer set!")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("set a timer for 10 minutes")

        call_kwargs = mock_api.messages.create.call_args[1]
        assert "thinking" not in call_kwargs

    @pytest.mark.asyncio
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_complex_query_gets_thinking(self, mock_prompt, mock_history, mock_client):
        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("Here's my analysis")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("what should I eat for dinner given my nutritional profile")

        call_kwargs = mock_api.messages.create.call_args[1]
        assert "thinking" in call_kwargs
        assert call_kwargs["thinking"]["budget_tokens"] == 64000

    @pytest.mark.asyncio
    @patch("aria_api.config")
    @patch("aria_api._get_client")
    @patch("aria_api.get_recent_turns", return_value=[])
    @patch("aria_api.build_primary_prompt", return_value="System prompt")
    async def test_always_think_overrides_bypass(self, mock_prompt, mock_history,
                                                  mock_client, mock_config):
        mock_config.ARIA_MODEL = "claude-opus-4-6-20250610"
        mock_config.ARIA_MAX_TOKENS = 16384
        mock_config.ARIA_THINKING_BUDGET = 64000
        mock_config.ARIA_ALWAYS_THINK = True

        mock_api = MagicMock()
        mock_client.return_value = mock_api
        mock_api.messages.create.return_value = _make_response(
            [_make_text_block("Timer set!")], stop_reason="end_turn"
        )

        await aria_api.ask_aria("set a timer for 10 minutes")

        call_kwargs = mock_api.messages.create.call_args[1]
        assert "thinking" in call_kwargs


class TestToolDefinitions:
    """Verify tool schemas are well-formed."""

    def test_all_tools_have_required_fields(self):
        for tool in aria_api.TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_tool_count(self):
        assert len(aria_api.TOOLS) == 6

    def test_tool_names_unique(self):
        names = [t["name"] for t in aria_api.TOOLS]
        assert len(names) == len(set(names))
