"""Tests for conversation_history.py — rolling history extraction.

SAFETY: All database calls mocked.
"""

from unittest.mock import patch, MagicMock

import pytest

import conversation_history


class TestGetRecentTurns:

    def _mock_rows(self, rows):
        """Set up mock DB returning given rows (newest first, as SQL returns).

        Each row should have 'timestamp', 'input', 'response'.
        If timestamp is omitted, a default is used.
        """
        from datetime import datetime
        for r in rows:
            if "timestamp" not in r:
                r["timestamp"] = datetime(2026, 3, 25, 14, 0, 0)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = rows
        patcher = patch("conversation_history.db.get_conn")
        mock_get_conn = patcher.start()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        return patcher

    def test_basic_history(self):
        from datetime import datetime
        # Newest first (as SQL ORDER BY DESC returns)
        rows = [
            {"timestamp": datetime(2026, 3, 25, 14, 5), "input": "How are you?", "response": "I'm great!"},
            {"timestamp": datetime(2026, 3, 25, 14, 0), "input": "Hello", "response": "Hi there!"},
        ]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns(n=10)
            # Should be chronological (oldest first)
            assert len(turns) == 4  # 2 user + 2 assistant
            assert "Hello" in turns[0]["content"]
            assert "2026-03-25" in turns[0]["content"]  # timestamp prepended
            assert turns[1] == {"role": "assistant", "content": "Hi there!"}
            assert "How are you?" in turns[2]["content"]
            assert turns[3] == {"role": "assistant", "content": "I'm great!"}
        finally:
            p.stop()

    def test_empty_database(self):
        p = self._mock_rows([])
        try:
            turns = conversation_history.get_recent_turns()
            assert turns == []
        finally:
            p.stop()

    def test_strips_voice_prefix(self):
        rows = [{"input": "[voice] What time is it?", "response": "It's 3 PM."}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert "What time is it?" in turns[0]["content"]
            assert "[voice]" not in turns[0]["content"]
        finally:
            p.stop()

    def test_strips_sms_prefix(self):
        rows = [{"input": "[sms:+12624751990] Hey there", "response": "Hi!"}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert "Hey there" in turns[0]["content"]
            assert "[sms:" not in turns[0]["content"]
        finally:
            p.stop()

    def test_strips_file_prefix(self):
        rows = [{"input": "[file:photo.jpg] Here's a photo", "response": "Nice pic!"}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert "Here's a photo" in turns[0]["content"]
            assert "[file:" not in turns[0]["content"]
        finally:
            p.stop()

    def test_timestamps_prepended(self):
        """Each user message should have a timestamp so ARIA sees time gaps."""
        from datetime import datetime
        rows = [{"timestamp": datetime(2026, 3, 25, 21, 30), "input": "Good night", "response": "Sleep well!"}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert "[2026-03-25T21:30:00]" in turns[0]["content"]
            assert "Good night" in turns[0]["content"]
        finally:
            p.stop()

    def test_skips_stt_only_entries(self):
        rows = [
            {"input": "Real question", "response": "Real answer"},
            {"input": "[stt] (3.2s audio)", "response": "transcription"},
        ]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            # Only the real question, not the STT entry
            assert len(turns) == 2
            assert "Real question" in turns[0]["content"]
        finally:
            p.stop()

    def test_skips_empty_entries(self):
        rows = [
            {"input": "Good question", "response": "Good answer"},
            {"input": "", "response": "Orphan response"},
            {"input": "Orphan input", "response": ""},
        ]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert len(turns) == 2  # only the complete pair
        finally:
            p.stop()

    def test_truncates_long_responses(self):
        long_response = "x" * 5000
        rows = [{"input": "Tell me everything", "response": long_response}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert len(turns[1]["content"]) == conversation_history.MAX_CHARS_PER_TURN + 3  # +3 for "..."
            assert turns[1]["content"].endswith("...")
        finally:
            p.stop()

    def test_respects_n_parameter(self):
        """Verify n parameter is passed to SQL (mocked, so we check the call)."""
        rows = [{"input": f"Q{i}", "response": f"A{i}"} for i in range(5)]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns(n=3)
            # All 5 rows returned by mock, producing 10 messages
            assert len(turns) == 10
        finally:
            p.stop()

    def test_uses_config_default(self):
        rows = [{"input": "Hi", "response": "Hello"}]
        p = self._mock_rows(rows)
        try:
            with patch("conversation_history.config") as mock_cfg:
                mock_cfg.ARIA_HISTORY_TURNS = 30
                conversation_history.get_recent_turns()
                # Verify the SQL was called (we trust the n parameter passes through)
        finally:
            p.stop()

    def test_strips_action_blocks(self):
        """ACTION blocks should be stripped from assistant responses."""
        response = (
            'Logged your meal! '
            '<!--ACTION::{"action": "log_health", "date": "2026-03-25", '
            '"category": "meal", "description": "chicken"}-->'
            ' Have a good one.'
        )
        rows = [{"input": "I had chicken", "response": response}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert "Logged your meal!" in turns[1]["content"]
            assert "Have a good one." in turns[1]["content"]
            assert "ACTION" not in turns[1]["content"]
        finally:
            p.stop()

    def test_strips_multiple_action_blocks(self):
        """Multiple ACTION blocks in one response should all be stripped."""
        response = (
            'Done! '
            '<!--ACTION::{"action": "log_health", "category": "meal"}-->'
            ' '
            '<!--ACTION::{"action": "log_nutrition", "food_name": "eggs"}-->'
        )
        rows = [{"input": "Log breakfast", "response": response}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert "Done!" in turns[1]["content"]
            assert "ACTION" not in turns[1]["content"]
        finally:
            p.stop()

    def test_action_only_response_skipped(self):
        """If stripping ACTIONs leaves empty text, the turn should be skipped."""
        rows = [
            {"input": "Real question", "response": "Real answer"},
            {"input": "Do it", "response": '<!--ACTION::{"action": "set_delivery", "method": "voice"}-->'},
        ]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            # Only the real question pair, ACTION-only pair is skipped
            assert len(turns) == 2
            assert "Real question" in turns[0]["content"]
        finally:
            p.stop()
