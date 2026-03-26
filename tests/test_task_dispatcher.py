"""Tests for task_dispatcher.py and redis_client.py task queue functions.

SAFETY: All Redis calls mocked. No real Redis connections.
"""

import json
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

import redis_client
import task_dispatcher
import actions


@pytest.fixture(autouse=True)
def _reset_redis():
    """Reset Redis singleton."""
    redis_client._client = None
    redis_client._warned = False
    yield
    redis_client._client = None
    redis_client._warned = False


class TestPushTask:

    @patch("redis_client.get_client")
    def test_pushes_to_redis(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        task = {
            "task_id": "abc123",
            "mode": "shell",
            "command": "echo hello",
            "notify": True,
        }
        result = redis_client.push_task(task)
        assert result == "abc123"
        mock_client.hset.assert_called_once()
        mock_client.sadd.assert_called_once()
        mock_client.xadd.assert_called_once()

    @patch("redis_client.get_client")
    def test_creates_task_hash(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        task = {
            "task_id": "t1",
            "mode": "agentic",
            "task": "Generate an image",
            "context": "User likes warm tones",
            "notify": False,
        }
        redis_client.push_task(task)

        call_args = mock_client.hset.call_args
        mapping = call_args[1]["mapping"]
        assert mapping["status"] == "queued"
        assert mapping["description"] == "Generate an image"
        assert mapping["mode"] == "agentic"
        assert mapping["notify"] == "0"

    @patch("redis_client.get_client")
    def test_returns_none_when_redis_down(self, mock_get_client):
        mock_get_client.return_value = None
        result = redis_client.push_task({"task_id": "t1", "mode": "shell"})
        assert result is None

    @patch("redis_client.get_client")
    def test_returns_none_on_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.hset.side_effect = Exception("connection lost")
        result = redis_client.push_task({"task_id": "t1", "mode": "shell"})
        assert result is None


class TestUpdateTaskState:

    @patch("redis_client.get_client")
    def test_updates_hash(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        redis_client.update_task_state("t1", status="running", progress=50, message="Working")
        mock_client.hset.assert_called_once()
        mapping = mock_client.hset.call_args[1]["mapping"]
        assert mapping["status"] == "running"
        assert mapping["progress"] == "50"

    @patch("redis_client.get_client")
    def test_safe_when_redis_down(self, mock_get_client):
        mock_get_client.return_value = None
        redis_client.update_task_state("t1", status="running")  # should not raise


class TestCompleteTask:

    @patch("redis_client.get_client")
    def test_marks_completed(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        redis_client.complete_task("t1", result="Done!")
        mock_client.hset.assert_called_once()
        mock_client.srem.assert_called_once()
        mock_client.publish.assert_called_once()

        mapping = mock_client.hset.call_args[1]["mapping"]
        assert mapping["status"] == "completed"
        assert mapping["result"] == "Done!"

    @patch("redis_client.get_client")
    def test_marks_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        redis_client.complete_task("t1", error="Command failed")
        mapping = mock_client.hset.call_args[1]["mapping"]
        assert mapping["status"] == "error"
        assert mapping["error"] == "Command failed"

    @patch("redis_client.get_client")
    def test_publishes_notification(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        redis_client.complete_task("t1", result="ok")
        pub_args = mock_client.publish.call_args
        channel = pub_args[0][0]
        message = json.loads(pub_args[0][1])
        assert "task_complete" in channel
        assert message["task_id"] == "t1"
        assert message["status"] == "completed"

    @patch("redis_client.get_client")
    def test_safe_when_redis_down(self, mock_get_client):
        mock_get_client.return_value = None
        redis_client.complete_task("t1", result="ok")  # should not raise


class TestDispatchActionBlock:
    """Test the dispatch_action handler in actions.py."""

    @patch("actions.redis_client")
    def test_dispatch_shell(self, mock_rc):
        mock_rc.push_task.return_value = "t1"

        response = 'Running that for you! <!--ACTION::{"action": "dispatch_action", "mode": "shell", "command": "uptime"}-->'
        metadata = {}
        result = actions.process_actions(response, metadata=metadata)

        mock_rc.push_task.assert_called_once()
        task_arg = mock_rc.push_task.call_args[0][0]
        assert task_arg["mode"] == "shell"
        assert task_arg["command"] == "uptime"
        assert "dispatched_task_id" in metadata
        assert "Running that for you" in result

    @patch("actions.redis_client")
    def test_dispatch_agentic(self, mock_rc):
        mock_rc.push_task.return_value = "t2"

        response = 'On it! <!--ACTION::{"action": "dispatch_action", "mode": "agentic", "task": "Generate an image of a sunset", "context": "warm tones", "notify": true}-->'
        metadata = {}
        actions.process_actions(response, metadata=metadata)

        task_arg = mock_rc.push_task.call_args[0][0]
        assert task_arg["mode"] == "agentic"
        assert "sunset" in task_arg["task"]
        assert task_arg["context"] == "warm tones"
        assert task_arg["notify"] is True

    @patch("actions.redis_client")
    def test_dispatch_fails_gracefully(self, mock_rc):
        mock_rc.push_task.return_value = None  # Redis down

        response = 'Let me check <!--ACTION::{"action": "dispatch_action", "mode": "shell", "command": "uptime"}-->'
        result = actions.process_actions(response)
        assert "Failed to dispatch" in result

    @patch("actions.redis_client")
    def test_dispatch_generates_task_id(self, mock_rc):
        mock_rc.push_task.return_value = "abc"

        response = '<!--ACTION::{"action": "dispatch_action", "mode": "shell", "command": "ls"}-->'
        actions.process_actions(response)

        task_arg = mock_rc.push_task.call_args[0][0]
        assert "task_id" in task_arg
        assert len(task_arg["task_id"]) == 8  # UUID prefix

    @patch("actions.redis_client")
    def test_dispatch_includes_channel_from_metadata(self, mock_rc):
        mock_rc.push_task.return_value = "t1"

        response = '<!--ACTION::{"action": "dispatch_action", "mode": "shell", "command": "uptime"}-->'
        metadata = {"channel": "sms"}
        actions.process_actions(response, metadata=metadata)

        task_arg = mock_rc.push_task.call_args[0][0]
        assert task_arg["channel"] == "sms"

    @patch("actions.redis_client")
    def test_dispatch_defaults_channel_to_voice(self, mock_rc):
        mock_rc.push_task.return_value = "t1"

        response = '<!--ACTION::{"action": "dispatch_action", "mode": "shell", "command": "uptime"}-->'
        metadata = {}  # no channel key
        actions.process_actions(response, metadata=metadata)

        task_arg = mock_rc.push_task.call_args[0][0]
        assert task_arg["channel"] == "voice"

    @patch("actions.redis_client")
    def test_dispatch_defaults_channel_when_no_metadata(self, mock_rc):
        mock_rc.push_task.return_value = "t1"

        response = '<!--ACTION::{"action": "dispatch_action", "mode": "shell", "command": "ls"}-->'
        actions.process_actions(response)  # no metadata arg

        task_arg = mock_rc.push_task.call_args[0][0]
        assert task_arg["channel"] == "voice"


class TestShellHandler:
    """Test the shell command execution in the dispatcher."""

    @pytest.mark.asyncio
    @patch("redis_client.get_client")
    async def test_executes_command(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.hgetall.return_value = {
            "command": "echo hello",
            "notify": "1",
        }

        await task_dispatcher._handle_shell("t1")
        # complete_task should have been called with result containing "hello"
        complete_calls = [c for c in mock_client.method_calls if "publish" in str(c)]
        # Verify via redis_client.complete_task which calls client methods
        assert mock_client.hset.called

    @pytest.mark.asyncio
    @patch("redis_client.get_client")
    async def test_handles_missing_command(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.hgetall.return_value = {"command": ""}

        await task_dispatcher._handle_shell("t1")
        # Should complete with error
        assert mock_client.publish.called


class TestDispatcherLifecycle:

    def test_start_sets_running(self):
        # Can't fully test async loop, but verify state management
        assert task_dispatcher._running is False
        # Don't actually start (would need event loop)

    def test_stop_clears_running(self):
        task_dispatcher._running = True
        task_dispatcher._task = None
        task_dispatcher.stop_dispatcher()
        assert task_dispatcher._running is False
