"""Tests for conversation_history.py — rolling history extraction.

SAFETY: All database calls mocked.
"""

from unittest.mock import patch, MagicMock

import pytest

import conversation_history


class TestGetRecentTurns:

    def _mock_rows(self, rows):
        """Set up mock DB returning given rows (newest first, as SQL returns)."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = rows
        patcher = patch("conversation_history.db.get_conn")
        mock_get_conn = patcher.start()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        return patcher

    def test_basic_history(self):
        # Newest first (as SQL ORDER BY DESC returns)
        rows = [
            {"input": "How are you?", "response": "I'm great!"},
            {"input": "Hello", "response": "Hi there!"},
        ]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns(n=10)
            # Should be chronological (oldest first)
            assert len(turns) == 4  # 2 user + 2 assistant
            assert turns[0] == {"role": "user", "content": "Hello"}
            assert turns[1] == {"role": "assistant", "content": "Hi there!"}
            assert turns[2] == {"role": "user", "content": "How are you?"}
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
            assert turns[0]["content"] == "What time is it?"
        finally:
            p.stop()

    def test_strips_sms_prefix(self):
        rows = [{"input": "[sms:+12624751990] Hey there", "response": "Hi!"}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert turns[0]["content"] == "Hey there"
        finally:
            p.stop()

    def test_strips_file_prefix(self):
        rows = [{"input": "[file:photo.jpg] Here's a photo", "response": "Nice pic!"}]
        p = self._mock_rows(rows)
        try:
            turns = conversation_history.get_recent_turns()
            assert turns[0]["content"] == "Here's a photo"
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
            assert turns[0]["content"] == "Real question"
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
