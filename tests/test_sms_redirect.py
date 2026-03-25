"""Tests for SMS → Image redirect in sms.py.

Temporary feature: all outbound SMS rendered as images and pushed to phone
while A2P 10DLC carrier registration is pending.

SAFETY: All push_image calls and DB writes are mocked. No real phone pushes.
"""

import os
from unittest.mock import patch, MagicMock, call

from PIL import Image

import sms


class TestRenderSmsImage:
    """Tests for _render_sms_image() — text-to-PNG rendering."""

    def test_produces_valid_png(self):
        path = sms._render_sms_image("Hello, world!")
        try:
            assert os.path.exists(path)
            img = Image.open(path)
            assert img.format == "PNG"
            assert img.width == 540
        finally:
            os.unlink(path)

    def test_short_message_reasonable_height(self):
        path = sms._render_sms_image("Short message.")
        try:
            img = Image.open(path)
            # Header block (~70) + 1 line (~22) + padding (48) ≈ ~140
            assert img.height < 250
        finally:
            os.unlink(path)

    def test_long_message_scales_height(self):
        body = "This is a paragraph.\n\n" * 20
        path = sms._render_sms_image(body)
        try:
            img = Image.open(path)
            # 20+ lines should produce a tall image
            assert img.height > 400
        finally:
            os.unlink(path)

    def test_preserves_paragraphs(self):
        """Text with blank lines produces more lines than without."""
        single = "Line one. Line two. Line three."
        multi = "Line one.\n\nLine two.\n\nLine three."
        path_s = sms._render_sms_image(single)
        path_m = sms._render_sms_image(multi)
        try:
            img_s = Image.open(path_s)
            img_m = Image.open(path_m)
            # Multi-paragraph version should be taller (blank lines add height)
            assert img_m.height > img_s.height
        finally:
            os.unlink(path_s)
            os.unlink(path_m)

    def test_empty_body(self):
        path = sms._render_sms_image("")
        try:
            assert os.path.exists(path)
            img = Image.open(path)
            assert img.width == 540
        finally:
            os.unlink(path)

    def test_none_body(self):
        path = sms._render_sms_image(None)
        try:
            assert os.path.exists(path)
        finally:
            os.unlink(path)

    def test_unicode_chars(self):
        path = sms._render_sms_image("Caf\u00e9 r\u00e9sum\u00e9 \u2014 45\u00b0F \u2022 \u2713 done")
        try:
            assert os.path.exists(path)
            img = Image.open(path)
            assert img.format == "PNG"
        finally:
            os.unlink(path)

    def test_custom_header(self):
        """Custom header text doesn't crash."""
        path = sms._render_sms_image("Body text", header="Timer")
        try:
            assert os.path.exists(path)
        finally:
            os.unlink(path)

    def test_very_long_word_wraps(self):
        """URLs and long words don't crash or overflow."""
        body = "Check this: " + "x" * 200 + " and this."
        path = sms._render_sms_image(body)
        try:
            img = Image.open(path)
            assert img.width == 540
        finally:
            os.unlink(path)


class TestRedirectToImage:
    """Tests for _redirect_to_image() — orchestration of render + push + log."""

    @patch("sms.db.get_conn")
    @patch("push_image.push_image")
    @patch("sms._render_sms_image")
    def test_calls_push_image(self, mock_render, mock_push, mock_db):
        mock_render.return_value = "/tmp/fake.png"
        mock_push.return_value = True
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        sms._redirect_to_image("+15551234567", "Hello")

        mock_push.assert_called_once_with("/tmp/fake.png", caption="ARIA")

    @patch("sms.db.get_conn")
    @patch("push_image.push_image")
    @patch("sms._render_sms_image")
    def test_returns_fake_sid(self, mock_render, mock_push, mock_db):
        mock_render.return_value = "/tmp/fake.png"
        mock_push.return_value = True
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        sid = sms._redirect_to_image("+15551234567", "Hello")

        assert sid.startswith("IMG_")
        assert len(sid) == 12  # "IMG_" + 8 hex chars

    @patch("sms.db.get_conn")
    @patch("push_image.push_image")
    @patch("sms._render_sms_image")
    def test_logs_to_sms_outbound(self, mock_render, mock_push, mock_db):
        mock_render.return_value = "/tmp/fake.png"
        mock_push.return_value = True
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        sms._redirect_to_image("+15551234567", "Hello", media_url="http://example.com/img.jpg")

        sql = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO sms_outbound" in sql
        params = mock_conn.execute.call_args[0][1]
        assert params[0] == "+15551234567"
        assert params[1] == "Hello"
        assert params[2] == "http://example.com/img.jpg"
        assert params[3].startswith("IMG_")

    @patch("sms.db.get_conn")
    def test_empty_body_skips_push(self, mock_db):
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        with patch("sms._render_sms_image") as mock_render:
            sid = sms._redirect_to_image("+15551234567", "")
            mock_render.assert_not_called()
            assert sid.startswith("IMG_")

    @patch("sms.db.get_conn")
    def test_whitespace_body_skips_push(self, mock_db):
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        with patch("sms._render_sms_image") as mock_render:
            sid = sms._redirect_to_image("+15551234567", "   \n  ")
            mock_render.assert_not_called()
            assert sid.startswith("IMG_")

    @patch("sms.db.get_conn")
    @patch("push_image.push_image")
    @patch("sms._render_sms_image")
    def test_push_failure_doesnt_crash(self, mock_render, mock_push, mock_db):
        mock_render.return_value = "/tmp/fake.png"
        mock_push.return_value = False  # push failed
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        sid = sms._redirect_to_image("+15551234567", "Hello")

        assert sid.startswith("IMG_")
        # Still logs even on push failure
        assert mock_conn.execute.called

    @patch("sms.db.get_conn")
    @patch("sms._render_sms_image")
    def test_render_failure_doesnt_crash(self, mock_render, mock_db):
        mock_render.side_effect = RuntimeError("Pillow exploded")
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        sid = sms._redirect_to_image("+15551234567", "Hello")

        assert sid.startswith("IMG_")

    @patch("sms.db.get_conn")
    @patch("push_image.push_image")
    @patch("sms._render_sms_image")
    def test_cleans_up_temp_file(self, mock_render, mock_push, mock_db):
        import tempfile
        # Create a real temp file to verify it gets cleaned up
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        mock_render.return_value = tmp
        mock_push.return_value = True
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        sms._redirect_to_image("+15551234567", "Hello")

        assert not os.path.exists(tmp)


class TestSendSmsRedirect:
    """Tests for send_sms() flag-controlled redirect."""

    @patch("sms._redirect_to_image")
    def test_redirect_when_flag_true(self, mock_redirect):
        mock_redirect.return_value = "IMG_abc12345"

        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True):
            sid = sms.send_sms("+15551234567", "Test message")

        assert sid == "IMG_abc12345"
        mock_redirect.assert_called_once_with("+15551234567", "Test message", None)

    @patch("sms.db.get_conn")
    def test_normal_when_flag_false(self, mock_db):
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.sid = "SM_real_sid"
        mock_client.messages.create.return_value = mock_message
        sms._client = mock_client

        try:
            with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", False, create=True):
                sid = sms.send_sms("+15551234567", "Test")

            assert sid == "SM_real_sid"
            mock_client.messages.create.assert_called_once()
        finally:
            sms._client = None

    @patch("sms.db.get_conn")
    def test_normal_when_flag_missing(self, mock_db):
        """getattr default ensures normal path when flag not in config."""
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.sid = "SM_real_sid"
        mock_client.messages.create.return_value = mock_message
        sms._client = mock_client

        try:
            # Temporarily remove the attribute if it exists
            had_attr = hasattr(sms.config, "SMS_REDIRECT_TO_IMAGE")
            old_val = getattr(sms.config, "SMS_REDIRECT_TO_IMAGE", None)
            if had_attr:
                delattr(sms.config, "SMS_REDIRECT_TO_IMAGE")

            sid = sms.send_sms("+15551234567", "Test")
            assert sid == "SM_real_sid"
        finally:
            sms._client = None
            if had_attr:
                setattr(sms.config, "SMS_REDIRECT_TO_IMAGE", old_val)

    @patch("sms._redirect_to_image")
    def test_media_url_passed_through(self, mock_redirect):
        mock_redirect.return_value = "IMG_abc12345"

        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True):
            sms.send_sms("+15551234567", "Photo", media_url="http://example.com/img.jpg")

        mock_redirect.assert_called_once_with(
            "+15551234567", "Photo", "http://example.com/img.jpg"
        )


class TestSendLongSmsRedirect:
    """Tests for send_long_sms() — no splitting in redirect mode."""

    @patch("sms.send_sms")
    def test_no_splitting_when_redirect(self, mock_send):
        mock_send.return_value = "IMG_abc12345"

        long_body = "A" * 3000  # Would normally split into 2 chunks
        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True):
            sids = sms.send_long_sms("+15551234567", long_body)

        # Single call with full body — no splitting
        assert mock_send.call_count == 1
        assert mock_send.call_args[0][1] == long_body
        assert sids == ["IMG_abc12345"]

    @patch("sms.send_sms")
    def test_returns_single_element_list(self, mock_send):
        mock_send.return_value = "IMG_xyz"

        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True):
            result = sms.send_long_sms("+15551234567", "Short")

        assert isinstance(result, list)
        assert len(result) == 1

    @patch("sms.send_sms")
    def test_media_url_passed_through(self, mock_send):
        mock_send.return_value = "IMG_abc"

        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True):
            sms.send_long_sms("+15551234567", "Text", media_url="http://example.com/img.jpg")

        mock_send.assert_called_once_with(
            "+15551234567", "Text", "http://example.com/img.jpg"
        )

    @patch("sms.send_sms", return_value="SM_real")
    def test_normal_splitting_when_flag_false(self, mock_send):
        long_body = "A" * 800 + "\n\n" + "B" * 800
        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", False, create=True):
            sids = sms.send_long_sms("+15551234567", long_body)

        # Normal splitting into 2 chunks
        assert mock_send.call_count == 2
        assert len(sids) == 2


class TestSendMmsRedirect:
    """Tests for send_mms() — direct media push in redirect mode."""

    @patch("sms._redirect_to_image")
    @patch("push_image.push_image")
    def test_pushes_media_and_text(self, mock_push, mock_redirect, tmp_path):
        media_file = tmp_path / "photo.jpg"
        media_file.write_bytes(b"JPEG data")
        mock_push.return_value = True
        mock_redirect.return_value = "IMG_abc"

        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True):
            sid = sms.send_mms("+15551234567", "Photo", str(media_file))

        # Media pushed directly
        mock_push.assert_called_once_with(str(media_file), caption="Attachment")
        # Text redirected
        mock_redirect.assert_called_once_with("+15551234567", "Photo")
        assert sid == "IMG_abc"

    @patch("sms._redirect_to_image", return_value="IMG_abc")
    @patch("push_image.push_image", return_value=True)
    @patch("sms.stage_media")
    def test_no_staging_when_redirect(self, mock_stage, mock_push, mock_redirect, tmp_path):
        media_file = tmp_path / "photo.jpg"
        media_file.write_bytes(b"JPEG data")

        with patch.object(sms.config, "SMS_REDIRECT_TO_IMAGE", True, create=True):
            sms.send_mms("+15551234567", "Photo", str(media_file))

        mock_stage.assert_not_called()
