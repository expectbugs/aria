"""Tests for sms.py — Twilio SMS/MMS integration.

SAFETY: All Twilio client operations are mocked. No real messages sent.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import sms


def _mock_twilio():
    """Create a mock Twilio client."""
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.sid = "SM_test_sid_12345"
    mock_client.messages.create.return_value = mock_message
    return mock_client


class TestSendSms:
    @patch("sms.db.get_conn")
    def test_sends_sms(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = _mock_twilio()
        sms._client = mock_client

        sid = sms.send_sms("+15551234567", "Test message")
        assert sid == "SM_test_sid_12345"
        mock_client.messages.create.assert_called_once()
        kwargs = mock_client.messages.create.call_args[1]
        assert kwargs["to"] == "+15551234567"
        assert kwargs["body"] == "Test message"

        sms._client = None

    @patch("sms.db.get_conn")
    def test_sends_mms_with_media(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = _mock_twilio()
        sms._client = mock_client

        sid = sms.send_sms("+15551234567", "Photo", media_url="https://example.com/img.jpg")
        kwargs = mock_client.messages.create.call_args[1]
        assert kwargs["media_url"] == ["https://example.com/img.jpg"]

        sms._client = None

    @patch("sms.db.get_conn")
    def test_logs_outbound(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = _mock_twilio()
        sms._client = mock_client

        sms.send_sms("+15551234567", "Test")
        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO sms_outbound" in sql

        sms._client = None


class TestSendToOwner:
    @patch("sms.send_sms")
    def test_sends_to_owner_number(self, mock_send):
        mock_send.return_value = "SM_123"
        sid = sms.send_to_owner("Hello")
        mock_send.assert_called_once_with(sms.config.OWNER_PHONE_NUMBER, "Hello", None)


class TestStageMedia:
    def test_stages_file(self, tmp_path):
        test_file = tmp_path / "test_image.png"
        test_file.write_bytes(b"fake png data")

        # Temporarily override MMS_OUTBOX
        original_outbox = sms.MMS_OUTBOX
        sms.MMS_OUTBOX = tmp_path / "outbox"
        sms.MMS_OUTBOX.mkdir()

        try:
            url = sms.stage_media(str(test_file))
            assert "mms_media" in url
            assert "test_image.png" in url
            # File should be copied to outbox
            staged = list(sms.MMS_OUTBOX.iterdir())
            assert len(staged) == 1
        finally:
            sms.MMS_OUTBOX = original_outbox

    def test_file_not_found(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            sms.stage_media("/nonexistent/path.png")


class TestSendMms:
    @patch("sms.send_sms")
    @patch("sms.stage_media")
    def test_stages_and_sends(self, mock_stage, mock_send):
        mock_stage.return_value = "https://example.com/mms_media/img.png"
        mock_send.return_value = "SM_456"

        sid = sms.send_mms("+15551234567", "Photo", "/path/to/image.png")
        mock_stage.assert_called_once_with("/path/to/image.png")
        mock_send.assert_called_once_with(
            "+15551234567", "Photo",
            media_url="https://example.com/mms_media/img.png"
        )


class TestSplitSms:
    def test_short_message_no_split(self):
        assert sms.split_sms("Hello there!") == ["Hello there!"]

    def test_empty_string(self):
        assert sms.split_sms("") == [""]

    def test_exact_max_length(self):
        msg = "x" * 1500
        assert sms.split_sms(msg) == [msg]

    def test_paragraph_split(self):
        p1 = "First paragraph. " * 50  # ~850 chars
        p2 = "Second paragraph. " * 50
        body = p1.strip() + "\n\n" + p2.strip()
        result = sms.split_sms(body, max_length=1000)
        assert len(result) == 2
        assert result[0].startswith("First paragraph.")
        assert result[1].startswith("Second paragraph.")

    def test_sentence_split_when_paragraph_too_long(self):
        body = "This is sentence one. This is sentence two. " * 30
        result = sms.split_sms(body, max_length=500)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 500

    def test_word_split_when_no_sentence_break(self):
        body = "word " * 400  # 2000 chars, no periods
        result = sms.split_sms(body, max_length=500)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 500

    def test_hard_cut_no_spaces(self):
        body = "x" * 3000
        result = sms.split_sms(body, max_length=1500)
        assert len(result) == 2
        assert len(result[0]) == 1500

    def test_preserves_all_words(self):
        body = "Para one.\n\nPara two with more words. Sentence two! Really? " * 20
        result = sms.split_sms(body, max_length=300)
        rejoined = " ".join(result)
        for word in body.split():
            assert word.strip() in rejoined

    def test_three_way_split(self):
        body = "A" * 500 + "\n\n" + "B" * 500 + "\n\n" + "C" * 500
        result = sms.split_sms(body, max_length=600)
        assert len(result) == 3

    def test_real_world_long_response(self):
        body = (
            "Good morning, Adam! Happy Day 8.\n\n"
            "You slept 7.2 hours with 91% efficiency. "
            "Resting heart rate is 62, HRV is 38ms.\n\n"
            "It's 45 degrees and partly cloudy right now. High of 68 today.\n\n"
            "No appointments on the calendar today.\n\n"
            "Diet day 8! Yesterday you hit 1,650 calories, 112g protein. "
            "Sodium was at 1,780 — just barely under your 1,800 target.\n\n"
            "In the news — a federal judge blocked the new age verification law."
        )
        result = sms.split_sms(body, max_length=300)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 300
        assert result[0].startswith("Good morning")


class TestSendLongSms:
    @patch("sms.send_sms", return_value="SM_sid")
    def test_short_message_single_send(self, mock_send):
        sids = sms.send_long_sms("+15551234567", "Short message")
        assert sids == ["SM_sid"]
        mock_send.assert_called_once()

    @patch("sms.send_sms", return_value="SM_sid")
    def test_long_message_multiple_sends(self, mock_send):
        body = "First paragraph.\n\n" + "x" * 1600
        sids = sms.send_long_sms("+15551234567", body)
        assert len(sids) > 1
        assert mock_send.call_count > 1

    @patch("sms.send_sms", return_value="SM_sid")
    def test_media_url_only_on_first(self, mock_send):
        body = "A" * 800 + "\n\n" + "B" * 800
        sms.send_long_sms("+15551234567", body, media_url="https://example.com/img.jpg")
        first_call = mock_send.call_args_list[0]
        last_call = mock_send.call_args_list[-1]
        assert first_call[1].get("media_url") == "https://example.com/img.jpg"
        assert last_call[1].get("media_url") is None


class TestSendLongToOwner:
    @patch("sms.send_long_sms", return_value=["SM_sid"])
    def test_delegates_to_send_long_sms(self, mock_send):
        sms.send_long_to_owner("Hello")
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert args[0] == sms.config.OWNER_PHONE_NUMBER
        assert args[1] == "Hello"


class TestValidateRequest:
    @patch("sms.RequestValidator")
    def test_valid_signature(self, mock_validator_cls):
        mock_validator = MagicMock()
        mock_validator.validate.return_value = True
        mock_validator_cls.return_value = mock_validator

        result = sms.validate_request(
            "https://example.com/sms", {"Body": "test"}, "valid_sig"
        )
        assert result is True

    @patch("sms.RequestValidator")
    def test_invalid_signature(self, mock_validator_cls):
        mock_validator = MagicMock()
        mock_validator.validate.return_value = False
        mock_validator_cls.return_value = mock_validator

        result = sms.validate_request(
            "https://example.com/sms", {"Body": "test"}, "bad_sig"
        )
        assert result is False
