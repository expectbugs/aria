"""Tests for ambient_audio.py — audio file management."""

import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import ambient_audio


class TestGetAudioDir:
    def test_creates_date_directory(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            dt = datetime(2026, 3, 20, 14, 0, 0)
            result = ambient_audio.get_audio_dir(dt)
            assert result == tmp_path / "2026-03-20"
            assert result.is_dir()

    def test_defaults_to_today(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            result = ambient_audio.get_audio_dir()
            today = datetime.now().strftime("%Y-%m-%d")
            assert result.name == today


class TestSaveAudio:
    def test_saves_file(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            audio = b"RIFF" + b"\x00" * 100
            dt = datetime(2026, 3, 20, 14, 23, 1)
            path = ambient_audio.save_audio(audio, started_at=dt, duration_s=7.2)
            assert os.path.exists(path)
            assert "142301" in path  # HHMMSS
            assert "7.2s" in path
            assert Path(path).read_bytes() == audio

    def test_handles_collision(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            audio = b"RIFF" + b"\x00" * 50
            dt = datetime(2026, 3, 20, 14, 23, 1)
            path1 = ambient_audio.save_audio(audio, started_at=dt, duration_s=5.0)
            path2 = ambient_audio.save_audio(audio, started_at=dt, duration_s=5.0)
            assert path1 != path2
            assert os.path.exists(path1)
            assert os.path.exists(path2)


class TestCleanupOld:
    def test_deletes_old_files(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            # Create an "old" day directory
            old_date = (datetime.now() - timedelta(hours=100))
            old_dir = tmp_path / old_date.strftime("%Y-%m-%d")
            old_dir.mkdir()
            old_file = old_dir / "seg_120000_5.0s.wav"
            old_file.write_bytes(b"old audio data")

            files_deleted, bytes_freed = ambient_audio.cleanup_old(
                retention_hours=72,
            )
            assert files_deleted == 1
            assert bytes_freed > 0
            assert not old_file.exists()

    def test_keeps_recent_files(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            # Create a "today" directory with a fresh file
            today_dir = tmp_path / datetime.now().strftime("%Y-%m-%d")
            today_dir.mkdir()
            recent_file = today_dir / "seg_120000_5.0s.wav"
            recent_file.write_bytes(b"recent audio data")

            files_deleted, _ = ambient_audio.cleanup_old(retention_hours=72)
            assert files_deleted == 0
            assert recent_file.exists()

    def test_removes_empty_old_dirs(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            old_date = (datetime.now() - timedelta(hours=200))
            old_dir = tmp_path / old_date.strftime("%Y-%m-%d")
            old_dir.mkdir()
            old_file = old_dir / "test.wav"
            old_file.write_bytes(b"data")

            ambient_audio.cleanup_old(retention_hours=72)
            assert not old_dir.exists()

    def test_no_base_dir(self, tmp_path):
        nonexistent = tmp_path / "nonexistent"
        with patch.object(ambient_audio, "_get_base_dir", return_value=nonexistent):
            files_deleted, bytes_freed = ambient_audio.cleanup_old()
            assert files_deleted == 0
            assert bytes_freed == 0

    def test_ignores_non_date_dirs(self, tmp_path):
        with patch.object(ambient_audio, "_get_base_dir", return_value=tmp_path):
            bad_dir = tmp_path / "not-a-date"
            bad_dir.mkdir()
            (bad_dir / "test.wav").write_bytes(b"data")

            files_deleted, _ = ambient_audio.cleanup_old(retention_hours=1)
            assert files_deleted == 0
