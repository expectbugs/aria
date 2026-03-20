"""Tests for push_image.py — image push to phone.

SAFETY: All HTTP requests to the phone are mocked.
"""

from unittest.mock import patch, MagicMock

import push_image


class TestPushImage:
    def test_success(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"PNG fake data")

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("push_image.httpx.post", return_value=mock_resp):
            assert push_image.push_image(str(img)) is True

    def test_file_not_found(self):
        assert push_image.push_image("/nonexistent/image.png") is False

    def test_phone_unreachable(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"PNG data")

        import httpx
        with patch("push_image.httpx.post", side_effect=httpx.ConnectError("")):
            assert push_image.push_image(str(img)) is False

    def test_content_type_detection(self, tmp_path):
        for ext, expected_ct in [
            (".png", "image/png"),
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".webp", "image/webp"),
        ]:
            img = tmp_path / f"test{ext}"
            img.write_bytes(b"fake data")
            mock_resp = MagicMock()
            mock_resp.status_code = 200

            with patch("push_image.httpx.post", return_value=mock_resp) as mock_post:
                push_image.push_image(str(img))
                # The content type is in the files tuple
                files_arg = mock_post.call_args[1]["files"]
                # files = {"image": (name, file, content_type)}
                _, _, ct = files_arg["image"]
                assert ct == expected_ct

    def test_unknown_extension_defaults_to_png(self, tmp_path):
        img = tmp_path / "test.bmp"
        img.write_bytes(b"BMP data")
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("push_image.httpx.post", return_value=mock_resp) as mock_post:
            push_image.push_image(str(img))
            files_arg = mock_post.call_args[1]["files"]
            _, _, ct = files_arg["image"]
            assert ct == "image/png"

    def test_url_uses_config(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"data")
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("push_image.httpx.post", return_value=mock_resp) as mock_post, \
             patch("push_image.config") as mock_config:
            mock_config.PHONE_IP = "100.5.6.7"
            mock_config.PHONE_PORT = 8888
            push_image.push_image(str(img))
            url = mock_post.call_args[0][0]
            assert "100.5.6.7:8888" in url
            assert "/image" in url
