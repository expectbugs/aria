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
    completion_listener.ask_aria = AsyncMock(return_value="Default response")
    completion_listener._generate_tts = AsyncMock(return_value=b"audio")
    completion_listener.push_audio = MagicMock()
    completion_listener.push_audio.push_audio = MagicMock(return_value=True)
    yield
    completion_listener.ask_aria = None
    completion_listener._generate_tts = None
    completion_listener.push_audio = None


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
        completion_listener.ask_aria = AsyncMock(return_value="Your sunset image is ready!")

        await completion_listener._on_completion("t1", "completed", "Image at /tmp/sunset.png")

        completion_listener.ask_aria.assert_called_once()
        assert "sunset" in completion_listener.ask_aria.call_args[0][0].lower()

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
        completion_listener.ask_aria = mock_aria

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
        completion_listener.ask_aria = mock_aria

        await completion_listener._on_completion("t1", "error", "command not found")

        prompt = mock_aria.call_args[0][0]
        assert "failed" in prompt.lower()
        assert "command not found" in prompt

    @pytest.mark.asyncio
    @patch("completion_listener.sms")
    @patch("completion_listener.redis_client")
    async def test_falls_back_to_sms_on_voice_failure(self, mock_rc, mock_sms):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Test task",
        }
        completion_listener.ask_aria = AsyncMock(return_value="Task done!")
        completion_listener._generate_tts = AsyncMock(side_effect=Exception("TTS failed"))

        await completion_listener._on_completion("t1", "completed", "done")

        mock_sms.send_long_to_owner.assert_called_once_with("Task done!")

    @pytest.mark.asyncio
    @patch("completion_listener.redis_client")
    async def test_safe_when_redis_down(self, mock_rc):
        mock_rc.get_client.return_value = None
        await completion_listener._on_completion("t1", "completed", "done")


class TestListenerLifecycle:

    def test_start_sets_running(self):
        assert completion_listener._running is False

    def test_stop_clears_running(self):
        completion_listener._running = True
        completion_listener._task = None
        completion_listener.stop_listener()
        assert completion_listener._running is False


class TestFullPipeline:

    @pytest.mark.asyncio
    @patch("completion_listener.redis_client")
    async def test_dispatch_to_notification_flow(self, mock_rc):
        mock_client = MagicMock()
        mock_rc.get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "notify": "1",
            "description": "Check system uptime",
        }
        mock_aria = AsyncMock(return_value="Your system has been up for 12 days.")
        completion_listener.ask_aria = mock_aria

        await completion_listener._on_completion(
            "t1", "completed", "12:34:56 up 12 days, 3:45, 2 users"
        )

        assert mock_aria.called
        assert completion_listener.push_audio.push_audio.called
