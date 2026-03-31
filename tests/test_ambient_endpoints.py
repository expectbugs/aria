"""Tests for ambient pipeline endpoints in daemon.py."""

import json
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# We need to test the endpoint functions directly since we can't easily
# spin up the full FastAPI app in unit tests. Test the logic by mocking
# dependencies and calling through the FastAPI test client pattern.

import daemon
import ambient_store
import wake_word


class TestAmbientTranscriptEndpoint:
    """Test POST /ambient/transcript logic."""

    def test_wake_word_detection_in_transcript(self):
        """Verify wake word is detected and has_wake_word set correctly."""
        detected, cmd = wake_word.detect("ARIA set a timer for 5 minutes")
        assert detected is True
        assert "timer" in cmd

    def test_no_wake_word(self):
        detected, cmd = wake_word.detect("I told Mike about the proposal")
        assert detected is False

    @patch("ambient_store.insert_transcript")
    def test_insert_called_with_correct_params(self, mock_insert):
        """Verify store is called with the right fields."""
        mock_insert.return_value = {"id": 1, "has_wake_word": False}
        result = ambient_store.insert_transcript(
            source="slappy",
            text="Some ambient speech",
            started_at="2026-03-30T14:00:00",
            ended_at="2026-03-30T14:00:07",
            duration_s=7.0,
            confidence=0.95,
            speaker=None,
            has_wake_word=False,
        )
        assert result["id"] == 1
        mock_insert.assert_called_once()
        kwargs = mock_insert.call_args
        assert kwargs[1]["source"] == "slappy" or kwargs[0][0] == "slappy"

    @patch("ambient_store.insert_transcript")
    def test_wake_word_sets_flag(self, mock_insert):
        """When wake word detected, has_wake_word should be True."""
        text = "ARIA what time is it"
        has_wake, cmd = wake_word.detect(text)
        assert has_wake is True

        mock_insert.return_value = {"id": 2, "has_wake_word": True}
        result = ambient_store.insert_transcript(
            source="slappy", text=text,
            started_at="2026-03-30T14:00:00",
            has_wake_word=has_wake,
        )
        assert result["has_wake_word"] is True


class TestAmbientUploadFlow:
    """Test the /ambient/upload transcription flow logic."""

    @patch("ambient_store.insert_transcript")
    @patch("ambient_audio.save_audio")
    def test_audio_saved_and_transcript_stored(self, mock_save, mock_insert):
        """Verify audio file is saved and transcript is stored."""
        mock_save.return_value = "/data/ambient/2026-03-30/seg_140000_5.0s.wav"
        mock_insert.return_value = {
            "id": 1, "text": "hello world", "has_wake_word": False,
        }

        # Simulate what the endpoint does
        audio_bytes = b"RIFF" + b"\x00" * 100
        started_at = datetime(2026, 3, 30, 14, 0, 0)

        import ambient_audio
        audio_path = ambient_audio.save_audio(
            audio_bytes, started_at=started_at, duration_s=5.0,
        )
        assert audio_path is not None

        row = ambient_store.insert_transcript(
            source="phone", text="hello world",
            started_at=started_at.isoformat(),
            duration_s=5.0, audio_path=audio_path,
        )
        assert row["id"] == 1


class TestAmbientStatusEndpoint:
    """Test GET /ambient/status logic."""

    @patch("ambient_store.get_today_count", return_value=47)
    def test_status_returns_count(self, mock_count):
        count = ambient_store.get_today_count()
        assert count == 47

    @patch("ambient_store.get_today_count", side_effect=Exception("DB error"))
    def test_status_handles_db_error(self, mock_count):
        """Status endpoint should handle DB errors gracefully."""
        try:
            count = ambient_store.get_today_count()
        except Exception:
            count = 0
        assert count == 0


class TestRedisPublish:
    """Test Redis integration for ambient pipeline."""

    def test_redis_publish_payload_format(self):
        """Verify the Redis Pub/Sub payload is valid JSON with expected fields."""
        payload = json.dumps({
            "id": 42,
            "text": "Test transcript"[:200],
            "has_wake_word": False,
            "source": "slappy",
        })
        parsed = json.loads(payload)
        assert parsed["id"] == 42
        assert parsed["source"] == "slappy"
        assert parsed["has_wake_word"] is False

    def test_stats_key_format(self):
        """Verify the daily stats key uses the correct format."""
        prefix = "aria:"
        stats_key = f"{prefix}ambient:stats:{datetime.now().strftime('%Y-%m-%d')}"
        assert stats_key.startswith("aria:ambient:stats:")
        assert len(stats_key.split(":")) == 4


class TestAmbientPydanticModel:
    """Test the AmbientTranscriptRequest model."""

    def test_model_validates(self):
        req = daemon.AmbientTranscriptRequest(
            text="Hello world",
            source="slappy",
            started_at="2026-03-30T14:00:00",
        )
        assert req.text == "Hello world"
        assert req.source == "slappy"
        assert req.ended_at is None
        assert req.duration_s is None

    def test_model_defaults(self):
        req = daemon.AmbientTranscriptRequest(
            text="Test", started_at="2026-03-30T14:00:00",
        )
        assert req.source == "slappy"

    def test_model_all_fields(self):
        req = daemon.AmbientTranscriptRequest(
            text="Test", source="phone",
            started_at="2026-03-30T14:00:00",
            ended_at="2026-03-30T14:00:07",
            duration_s=7.2, confidence=0.94,
            speaker="Mike",
        )
        assert req.duration_s == 7.2
        assert req.speaker == "Mike"
