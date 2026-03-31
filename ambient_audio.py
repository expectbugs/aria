"""Audio file management for the ambient capture pipeline.

Handles saving, organizing, and cleaning up ambient audio chunks
in the data/ambient/ directory tree.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import config

log = logging.getLogger("aria.ambient_audio")


def _get_base_dir() -> Path:
    """Get the ambient audio base directory from config."""
    return Path(getattr(config, "AMBIENT_AUDIO_DIR",
                        Path(config.DATA_DIR) / "ambient"))


def get_audio_dir(dt: datetime | None = None) -> Path:
    """Get the date-partitioned audio directory, creating it if needed.

    Returns: Path like data/ambient/2026-03-30/
    """
    if dt is None:
        dt = datetime.now()
    base = _get_base_dir()
    day_dir = base / dt.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir


def save_audio(audio_bytes: bytes, started_at: datetime | None = None,
               duration_s: float | None = None) -> str:
    """Save an audio chunk to the date-partitioned directory.

    Returns the path string (relative-friendly, stored in DB).
    Filename format: seg_{HHMMSS}_{duration}s.wav
    """
    if started_at is None:
        started_at = datetime.now()

    day_dir = get_audio_dir(started_at)

    dur_str = f"{duration_s:.1f}" if duration_s else "0.0"
    filename = f"seg_{started_at.strftime('%H%M%S')}_{dur_str}s.wav"
    filepath = day_dir / filename

    # Handle collision (unlikely but possible with rapid segments)
    if filepath.exists():
        stem = filepath.stem
        filepath = day_dir / f"{stem}_{os.getpid()}.wav"

    filepath.write_bytes(audio_bytes)
    log.debug("Saved ambient audio: %s (%d bytes)", filepath, len(audio_bytes))
    return str(filepath)


def cleanup_old(retention_hours: int | None = None) -> tuple[int, int]:
    """Delete audio files older than retention period.

    Returns (files_deleted, bytes_freed).
    Does NOT touch the database — caller should also run
    ambient_store.cleanup_audio() to null out audio_path.
    """
    if retention_hours is None:
        retention_hours = getattr(config, "AMBIENT_AUDIO_RETENTION_HOURS", 72)

    base = _get_base_dir()
    if not base.exists():
        return 0, 0

    cutoff = datetime.now() - timedelta(hours=retention_hours)
    files_deleted = 0
    bytes_freed = 0

    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue

        # Parse date from directory name
        try:
            dir_date = datetime.strptime(day_dir.name, "%Y-%m-%d")
        except ValueError:
            continue

        # If the entire day is older than cutoff, remove all files
        if dir_date.date() < cutoff.date():
            for f in day_dir.iterdir():
                if f.is_file():
                    bytes_freed += f.stat().st_size
                    f.unlink()
                    files_deleted += 1
            # Remove empty directory
            try:
                day_dir.rmdir()
            except OSError:
                pass  # not empty (non-audio files?)
            continue

        # Same day as cutoff — check individual file timestamps
        if dir_date.date() == cutoff.date():
            for f in day_dir.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                    bytes_freed += f.stat().st_size
                    f.unlink()
                    files_deleted += 1

    if files_deleted > 0:
        log.info("Ambient audio cleanup: %d files deleted, %.1f MB freed",
                 files_deleted, bytes_freed / (1024 * 1024))
    return files_deleted, bytes_freed
