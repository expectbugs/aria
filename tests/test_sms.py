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
