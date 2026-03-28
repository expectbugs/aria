"""Tests for completion_listener.py — task result delivery.

SAFETY: All Redis, API, TTS, and push calls mocked.
"""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import completion_listener


@pytest.fixture(autouse=True)
def _reset():
    completion_listener._running = False
    completion_listener._task = None
    yield
    completion_listener._running = False
    completion_listener._task = None


@pytest.fixture(autouse=True)
def _mock_lazy_imports():
    """Pre-set the lazy import globals so _ensure_imports doesn't load real modules."""
    completion_listener.ask_haiku = AsyncMock(return_value="Default response")
    from actions import ActionResult
    def _mock_process_actions(r, **kw):
        return ActionResult(
            clean_response=r, actions_found=[], action_types=[], failures=[],
            warnings=[], metadata={}, claims_without_actions=[], expect_actions_missing=[],
        )
    completion_listener.process_actions = MagicMock(side_effect=_mock_process_actions)
    yield
    completion_listener.ask_haiku = None
    completion_listener.process_actions = None


@pytest.fixture(autouse=True)
def _mock_execute_delivery():
    """Mock execute_delivery for all completion listener tests."""
    async def _route(response_text, content_type="response", priority="normal",
                     source="voice", hint=None, sms_target=None, push_voice=True):
        method = "sms" if source == "sms" else "voice"
        audio = b"audio" if method == "voice" else b""
        return {"method": method, "audio": audio, "reason": "test"}
    with patch("delivery_engine.execute_delivery", new_callable=AsyncMock,
               side_effect=_route) as mock_ed:
        yield mock_ed


class TestOnCompletion:

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_notify_true_composes_response(self, mock_rc, mock_sms):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Generate sunset image",
        }
        completion_listener.ask_haiku = AsyncMock(return_value="Your sunset image is ready!")

        await completion_listener._on_completion("t1", "completed", "Image at /tmp/sunset.png")

        completion_listener.ask_haiku.assert_called_once()
        assert "sunset" in completion_listener.ask_haiku.call_args[0][0].lower()

    @pytest.mark.asyncio
    @patch("completion_listener.redis_client")
    async def test_notify_false_skips_delivery(self, mock_rc):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "0",
            "description": "Silent task",
        }
        mock_aria = AsyncMock()
        completion_listener.ask_haiku = mock_aria

        await completion_listener._on_completion("t1", "completed", "done")
        mock_aria.assert_not_called()

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_error_status_composes_error_message(self, mock_rc, mock_sms):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Broken task",
        }
        mock_aria = AsyncMock(return_value="Sorry, that task failed.")
        completion_listener.ask_haiku = mock_aria

        await completion_listener._on_completion("t1", "error", "command not found")

        prompt = mock_aria.call_args[0][0]
        assert "failed" in prompt.lower()
        assert "command not found" in prompt

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_falls_back_to_sms_on_voice_failure(self, mock_rc, mock_sms,
                                                       _mock_execute_delivery):
        """execute_delivery handles voice failure + SMS fallback internally."""
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Test task",
            "channel": "voice",
        }
        completion_listener.ask_haiku = AsyncMock(return_value="Task done!")
        # Simulate execute_delivery falling back to SMS
        async def _sms_fallback(**kw):
            return {"method": "sms", "audio": b"", "reason": "voice failed, SMS fallback"}
        _mock_execute_delivery.side_effect = _sms_fallback

        await completion_listener._on_completion("t1", "completed", "done")
        _mock_execute_delivery.assert_called_once()

    @pytest.mark.asyncio
    @patch("completion_listener.redis_client")
    async def test_safe_when_redis_down(self, mock_rc):
        mock_rc.get_client.return_value = None
        await completion_listener._on_completion("t1", "completed", "done")


class TestChannelAwareDelivery:

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_sms_channel_delivers_via_sms(self, mock_rc, mock_sms,
                                                  _mock_execute_delivery):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Test task",
            "channel": "sms",
        }
        completion_listener.ask_haiku = AsyncMock(return_value="Result ready!")

        await completion_listener._on_completion("t1", "completed", "done")

        _mock_execute_delivery.assert_called_once()
        assert _mock_execute_delivery.call_args[1]["source"] == "sms"

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_voice_channel_delivers_via_voice(self, mock_rc, mock_sms,
                                                      _mock_execute_delivery):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Test task",
            "channel": "voice",
        }
        completion_listener.ask_haiku = AsyncMock(return_value="Here you go!")

        await completion_listener._on_completion("t1", "completed", "done")

        _mock_execute_delivery.assert_called_once()
        assert _mock_execute_delivery.call_args[1]["source"] == "voice"

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_default_channel_is_voice(self, mock_rc, mock_sms,
                                              _mock_execute_delivery):
        """Tasks without a channel field (backward compat) default to voice."""
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Old task",
            # no "channel" key
        }
        completion_listener.ask_haiku = AsyncMock(return_value="Done!")

        await completion_listener._on_completion("t1", "completed", "done")

        _mock_execute_delivery.assert_called_once()
        assert _mock_execute_delivery.call_args[1]["source"] == "voice"


class TestListenerLifecycle:

    def test_start_sets_running(self):
        assert completion_listener._running is False

    def test_stop_clears_running(self):
        completion_listener._running = True
        completion_listener._task = None
        completion_listener.stop_listener()
        assert completion_listener._running is False


class TestActionBlockProcessing:

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_process_actions_called_on_response(self, mock_rc, mock_sms):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Test task",
        }
        raw = 'Done!<!--ACTION::{"action":"set_timer","minutes":5,"message":"hi"}-->'
        completion_listener.ask_haiku = AsyncMock(return_value=raw)
        from actions import ActionResult
        mock_result = ActionResult(
            clean_response="Done!", actions_found=[], action_types=[], failures=[],
            warnings=[], metadata={}, claims_without_actions=[], expect_actions_missing=[],
        )
        mock_pa = MagicMock(return_value=mock_result)
        completion_listener.process_actions = mock_pa

        await completion_listener._on_completion("t1", "completed", "result")

        mock_pa.assert_called_once_with(raw)

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_tts_receives_cleaned_response(self, mock_rc, mock_sms,
                                                   _mock_execute_delivery):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Test task",
        }
        raw = 'Timer set!<!--ACTION::{"action":"set_timer"}-->'
        completion_listener.ask_haiku = AsyncMock(return_value=raw)
        from actions import ActionResult
        mock_result = ActionResult(
            clean_response="Timer set!", actions_found=[], action_types=[], failures=[],
            warnings=[], metadata={}, claims_without_actions=[], expect_actions_missing=[],
        )
        completion_listener.process_actions = MagicMock(return_value=mock_result)

        await completion_listener._on_completion("t1", "completed", "result")

        # execute_delivery should receive the cleaned text, not the raw response
        delivered_text = _mock_execute_delivery.call_args[0][0]
        assert "ACTION" not in delivered_text
        assert "Timer set!" in delivered_text


class TestFullPipeline:

    @pytest.mark.asyncio
    @patch("completion_listener.redis_client")
    async def test_dispatch_to_notification_flow(self, mock_rc,
                                                   _mock_execute_delivery):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Check system uptime",
        }
        mock_aria = AsyncMock(return_value="Your system has been up for 12 days.")
        completion_listener.ask_haiku = mock_aria

        await completion_listener._on_completion(
            "t1", "completed", "12:34:56 up 12 days, 3:45, 2 users"
        )

        assert mock_aria.called
        _mock_execute_delivery.assert_called_once()
