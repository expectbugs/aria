"""Tests for WebSocket /ws/stt endpoint — streaming STT protocol.

SAFETY: Whisper engine is mocked. No GPU model loading.
"""

import json
import struct
import numpy as np
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from starlette.testclient import TestClient

import daemon
import config
import whisper_engine


@pytest.fixture
def client():
    with patch("daemon.db.get_pool"), patch("daemon.db.close"):
        with TestClient(daemon.app) as c:
            yield c


AUTH_HEADERS = {"authorization": f"Bearer {config.AUTH_TOKEN}"}


class TestWebSocketAuth:
    def test_reject_no_auth(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/stt"):
                pass

    def test_reject_bad_auth(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/stt", headers={"authorization": "Bearer wrong"}
            ):
                pass


class TestWebSocketDisabled:
    def test_close_when_whisper_disabled(self, client):
        with patch.object(config, "ENABLE_WHISPER", False):
            with pytest.raises(Exception):
                with client.websocket_connect("/ws/stt", headers=AUTH_HEADERS):
                    pass


class TestWebSocketProtocol:
    @patch("whisper_engine.EnergyVAD")
    @patch("whisper_engine.get_engine")
    @patch.object(config, "ENABLE_WHISPER", True)
    def test_config_message(self, mock_get_engine, mock_vad_cls, client):
        mock_get_engine.return_value = MagicMock()
        mock_vad_cls.return_value = MagicMock(
            process_chunk=MagicMock(return_value=None),
            flush=MagicMock(return_value=None),
        )

        with client.websocket_connect("/ws/stt", headers=AUTH_HEADERS) as ws:
            ws.send_json({"type": "config", "sample_rate": 16000})
            resp = ws.receive_json()
            assert resp["type"] == "ready"
            ws.send_json({"type": "stop"})

    @patch("whisper_engine.EnergyVAD")
    @patch("whisper_engine.get_engine")
    @patch.object(config, "ENABLE_WHISPER", True)
    def test_stop_message_closes_cleanly(self, mock_get_engine, mock_vad_cls, client):
        mock_get_engine.return_value = MagicMock()
        vad = MagicMock()
        vad.process_chunk.return_value = None
        vad.flush.return_value = None
        mock_vad_cls.return_value = vad

        with client.websocket_connect("/ws/stt", headers=AUTH_HEADERS) as ws:
            ws.send_json({"type": "config", "sample_rate": 16000})
            ws.receive_json()  # ready
            ws.send_json({"type": "stop"})
            # Connection should close cleanly

    @patch("daemon.log_request")
    @patch("whisper_engine.EnergyVAD")
    @patch("whisper_engine.get_engine")
    @patch.object(config, "ENABLE_WHISPER", True)
    def test_binary_audio_processed_by_vad(self, mock_get_engine, mock_vad_cls,
                                            mock_log, client):
        mock_get_engine.return_value = MagicMock()

        vad = MagicMock()
        vad.process_chunk.side_effect = [None, None]
        vad.flush.return_value = None
        mock_vad_cls.return_value = vad

        # Create a small PCM chunk (int16)
        pcm = np.zeros(1600, dtype=np.int16)  # 0.1s at 16kHz
        audio_bytes = pcm.tobytes()

        with client.websocket_connect("/ws/stt", headers=AUTH_HEADERS) as ws:
            ws.send_json({"type": "config", "sample_rate": 16000})
            ws.receive_json()  # ready
            ws.send_bytes(audio_bytes)
            ws.send_bytes(audio_bytes)
            ws.send_json({"type": "stop"})

        assert vad.process_chunk.call_count == 2

    @patch("daemon.log_request")
    @patch("daemon.asyncio.to_thread", new_callable=AsyncMock)
    @patch("whisper_engine.EnergyVAD")
    @patch("whisper_engine.get_engine")
    @patch.object(config, "ENABLE_WHISPER", True)
    def test_utterance_returns_transcript(self, mock_get_engine, mock_vad_cls,
                                           mock_to_thread, mock_log, client):
        mock_get_engine.return_value = MagicMock()

        # VAD returns an utterance on flush
        utterance = np.random.randn(16000).astype(np.float32)  # 1s audio
        vad = MagicMock()
        vad.process_chunk.return_value = None
        vad.flush.return_value = utterance
        mock_vad_cls.return_value = vad

        # Mock the transcription result
        mock_to_thread.return_value = whisper_engine.TranscriptResult(
            text="Hello world", duration=1.0, processing_time=0.2,
        )

        pcm = np.zeros(1600, dtype=np.int16).tobytes()

        with client.websocket_connect("/ws/stt", headers=AUTH_HEADERS) as ws:
            ws.send_json({"type": "config", "sample_rate": 16000})
            ws.receive_json()  # ready
            ws.send_bytes(pcm)
            ws.send_json({"type": "stop"})

            # Should receive a transcript
            resp = ws.receive_json()
            assert resp["type"] == "transcript"
            assert resp["text"] == "Hello world"
