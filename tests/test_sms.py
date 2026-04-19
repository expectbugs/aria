"""Tests for sms.py — Telnyx SMS/MMS integration.

SAFETY: All Telnyx client operations are mocked. No real messages sent.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import sms


def _mock_telnyx():
    """Create a mock Telnyx client with messages.send() returning a response."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.data.id = "msg_test_id_12345"
    mock_client.messages.send.return_value = mock_response
    return mock_client


class TestSendSms:
    @patch("sms.db.get_conn")
    def test_sends_sms(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = _mock_telnyx()
        sms._client = mock_client

        msg_id = sms.send_sms("+15551234567", "Test message")
        assert msg_id == "msg_test_id_12345"
        mock_client.messages.send.assert_called_once()
        kwargs = mock_client.messages.send.call_args[1]
        assert kwargs["to"] == "+15551234567"
        assert kwargs["text"] == "Test message"
        assert kwargs["from_"] == sms.config.TELNYX_PHONE_NUMBER

        sms._client = None

    @patch("sms.db.get_conn")
    def test_sends_mms_with_media(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = _mock_telnyx()
        sms._client = mock_client

        msg_id = sms.send_sms("+15551234567", "Photo", media_url="https://example.com/img.jpg")
        kwargs = mock_client.messages.send.call_args[1]
        assert kwargs["media_urls"] == ["https://example.com/img.jpg"]

        sms._client = None

    @patch("sms.db.get_conn")
    def test_logs_outbound(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = _mock_telnyx()
        sms._client = mock_client

        sms.send_sms("+15551234567", "Test")
        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO sms_outbound" in sql

        sms._client = None


class TestSendToOwner:
    @patch("sms.send_sms")
    def test_sends_to_owner_number(self, mock_send):
        mock_send.return_value = "msg_123"
        msg_id = sms.send_to_owner("Hello")
        mock_send.assert_called_once_with(sms.config.OWNER_PHONE_NUMBER, "Hello", None)


class TestStageMedia:
    @patch.object(sms.config, "TELNYX_WEBHOOK_URL",
                  "https://example.tail.ts.net/webhook/sms", create=True)
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
            assert url.startswith("https://example.tail.ts.net/webhook/mms_media/")
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
        mock_send.return_value = "msg_456"

        msg_id = sms.send_mms("+15551234567", "Photo", "/path/to/image.png")
        mock_stage.assert_called_once_with("/path/to/image.png")
        mock_send.assert_called_once_with(
            "+15551234567", "Photo",
            media_url="https://example.com/mms_media/img.png"
        )


class TestNormalizeForSms:
    """GSM-7 normalization prevents 22-segment UCS-2 explosions."""

    def test_plain_ascii_unchanged_and_gsm7(self):
        text, is_gsm7 = sms._normalize_for_sms("Hello, world!")
        assert text == "Hello, world!"
        assert is_gsm7 is True

    def test_em_dash_to_hyphen(self):
        text, is_gsm7 = sms._normalize_for_sms("Hey \u2014 how are you?")
        assert text == "Hey - how are you?"
        assert is_gsm7 is True

    def test_en_dash_to_hyphen(self):
        text, is_gsm7 = sms._normalize_for_sms("Range: 5\u201310")
        assert text == "Range: 5-10"
        assert is_gsm7 is True

    def test_smart_quotes_to_straight(self):
        text, is_gsm7 = sms._normalize_for_sms("\u201CHello\u201D she said \u2018hi\u2019")
        assert text == '"Hello" she said \'hi\''
        assert is_gsm7 is True

    def test_ellipsis_to_three_dots(self):
        text, is_gsm7 = sms._normalize_for_sms("Wait\u2026")
        assert text == "Wait..."
        assert is_gsm7 is True

    def test_backtick_to_apostrophe(self):
        """Backtick is ASCII 0x60 but NOT in GSM-7 — this specific bug caused
        the 22-segment 40302 error on 2026-04-18."""
        text, is_gsm7 = sms._normalize_for_sms("Check the `foo()` function")
        assert text == "Check the 'foo()' function"
        assert is_gsm7 is True

    def test_zero_width_chars_stripped(self):
        # Zero-width joiner/space commonly sneak in from copy-paste
        text, is_gsm7 = sms._normalize_for_sms("Hello\u200Bworld\u200D!")
        assert text == "Helloworld!"
        assert is_gsm7 is True

    def test_non_breaking_space_to_regular(self):
        text, is_gsm7 = sms._normalize_for_sms("Foo\u00A0bar")
        assert text == "Foo bar"
        assert is_gsm7 is True

    def test_copyright_and_trademark(self):
        text, is_gsm7 = sms._normalize_for_sms("\u00A9 2026 ARIA\u2122")
        assert text == "(c) 2026 ARIA(TM)"
        assert is_gsm7 is True

    def test_degree_and_math(self):
        text, is_gsm7 = sms._normalize_for_sms("45\u00B0F \u00B1 2")
        assert text == "45degF +/- 2"
        assert is_gsm7 is True

    def test_arrows_to_ascii(self):
        text, is_gsm7 = sms._normalize_for_sms("go \u2192 there")
        assert text == "go -> there"
        assert is_gsm7 is True

    def test_emoji_survives_as_ucs2(self):
        """Emoji can't be meaningfully substituted — message stays UCS-2."""
        text, is_gsm7 = sms._normalize_for_sms("Nice \U0001F525")
        assert "\U0001F525" in text
        assert is_gsm7 is False

    def test_cjk_survives_as_ucs2(self):
        text, is_gsm7 = sms._normalize_for_sms("hello \u4f60\u597d")
        assert "\u4f60\u597d" in text
        assert is_gsm7 is False

    def test_the_actual_ble_response_that_failed(self):
        """Exact reproduction of the 40302-triggering message from 2026-04-18."""
        body = ("Oh even better then. I peeked at g2code/app/src/input.ts \u2014 "
                "your Gesture type is literally `'tap' | 'double_tap' | "
                "'scroll_up' | 'scroll_down'` already")
        text, is_gsm7 = sms._normalize_for_sms(body)
        # Em-dash and backticks both substituted
        assert "\u2014" not in text
        assert "`" not in text
        assert is_gsm7 is True


class TestSplitSmsWithNormalization:
    """split_sms auto-picks max_length based on GSM-7-ness after normalization."""

    def test_gsm7_text_uses_1500_default(self):
        # 1200 chars of ASCII — fits in default GSM-7 budget (1500)
        body = "a" * 1200
        result = sms.split_sms(body)
        assert len(result) == 1

    def test_ucs2_text_uses_600_default(self):
        # 800 chars with an emoji — forces UCS-2, should split at 600
        body = "\U0001F525" + "a" * 799  # 800 chars total, forces UCS-2
        result = sms.split_sms(body)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 600

    def test_em_dash_text_stays_gsm7_after_normalization(self):
        # Long message with em-dashes — would be UCS-2 without normalization,
        # but substitution brings it back to GSM-7, so 1500 default applies
        body = "Hello \u2014 " + "text " * 250  # ~1260 chars with em-dashes
        result = sms.split_sms(body)
        assert len(result) == 1
        # Verify substitution actually happened
        assert "\u2014" not in result[0]
        assert "-" in result[0]

    def test_explicit_max_length_respected(self):
        body = "x" * 2000
        result = sms.split_sms(body, max_length=500)
        assert len(result) >= 4
        for chunk in result:
            assert len(chunk) <= 500


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
    @patch("sms.send_sms", return_value="msg_sid")
    def test_short_message_single_send(self, mock_send):
        sids = sms.send_long_sms("+15551234567", "Short message")
        assert sids == ["msg_sid"]
        mock_send.assert_called_once()

    @patch("sms.send_sms", return_value="msg_sid")
    def test_long_message_multiple_sends(self, mock_send):
        body = "First paragraph.\n\n" + "x" * 1600
        sids = sms.send_long_sms("+15551234567", body)
        assert len(sids) > 1
        assert mock_send.call_count > 1

    @patch("sms.send_sms", return_value="msg_sid")
    def test_media_url_only_on_first(self, mock_send):
        body = "A" * 800 + "\n\n" + "B" * 800
        sms.send_long_sms("+15551234567", body, media_url="https://example.com/img.jpg")
        first_call = mock_send.call_args_list[0]
        last_call = mock_send.call_args_list[-1]
        assert first_call[1].get("media_url") == "https://example.com/img.jpg"
        assert last_call[1].get("media_url") is None


class TestSendLongToOwner:
    @patch("sms.send_long_sms", return_value=["msg_sid"])
    def test_delegates_to_send_long_sms(self, mock_send):
        sms.send_long_to_owner("Hello")
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        assert args[0] == sms.config.OWNER_PHONE_NUMBER
        assert args[1] == "Hello"


class TestValidateRequest:
    def test_valid_signature(self):
        """Generate a real signature with a real keypair, verify it validates."""
        import base64, time
        from nacl.signing import SigningKey

        signing_key = SigningKey.generate()
        public_key_b64 = base64.b64encode(bytes(signing_key.verify_key)).decode()
        payload = '{"data": {}}'
        timestamp = str(int(time.time()))
        signed_payload = f"{timestamp}|{payload}".encode()
        signature_b64 = base64.b64encode(signing_key.sign(signed_payload).signature).decode()

        with patch.object(sms.config, "TELNYX_PUBLIC_KEY", public_key_b64, create=True):
            result = sms.validate_request(
                payload,
                {"webhook-id": "msg_123", "webhook-timestamp": timestamp,
                 "webhook-signature": signature_b64}
            )
        assert result is True

    def test_invalid_signature(self):
        """Wrong signature should fail verification."""
        import base64, time
        from nacl.signing import SigningKey

        signing_key = SigningKey.generate()
        public_key_b64 = base64.b64encode(bytes(signing_key.verify_key)).decode()
        timestamp = str(int(time.time()))

        with patch.object(sms.config, "TELNYX_PUBLIC_KEY", public_key_b64, create=True):
            result = sms.validate_request(
                '{"data": {}}',
                {"webhook-id": "msg_123", "webhook-timestamp": timestamp,
                 "webhook-signature": base64.b64encode(b"\x00" * 64).decode()}
            )
        assert result is False

    def test_stale_timestamp_rejected(self):
        """Timestamps older than 5 minutes must be rejected (replay protection)."""
        result = sms.validate_request(
            '{"data": {}}',
            {"webhook-id": "msg_123", "webhook-timestamp": "123456",
             "webhook-signature": "abc"}
        )
        assert result is False


class TestSendImageMms:
    @patch("sms.send_sms")
    @patch("sms.stage_media")
    def test_stages_and_sends(self, mock_stage, mock_send):
        mock_stage.return_value = "https://example.com/mms_media/img.png"
        mock_send.return_value = "msg_789"

        msg_id = sms.send_image_mms("+15551234567", "/path/to/image.png")
        mock_stage.assert_called_once_with("/path/to/image.png")
        mock_send.assert_called_once_with("+15551234567", "", media_url="https://example.com/mms_media/img.png")

    @patch("sms.send_sms")
    @patch("sms.stage_media")
    def test_with_body(self, mock_stage, mock_send):
        mock_stage.return_value = "https://example.com/mms_media/img.png"
        mock_send.return_value = "msg_789"

        sms.send_image_mms("+15551234567", "/path/to/image.png", body="Alert!")
        mock_send.assert_called_once_with("+15551234567", "Alert!", media_url="https://example.com/mms_media/img.png")

    def test_file_not_found(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            sms.send_image_mms("+15551234567", "/nonexistent/image.png")
