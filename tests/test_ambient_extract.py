"""Tests for ambient_extract.py — extraction engine and conversation grouping."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

import ambient_extract
from conftest import make_transcript_row


# ---------------------------------------------------------------------------
# Conversation boundary detection
# ---------------------------------------------------------------------------

class TestConversationBoundaries:
    def test_groups_by_time_gap(self):
        """Segments within 5 minutes are grouped; >5min gap splits."""
        t1 = make_transcript_row(id=1, started_at=datetime(2026, 3, 20, 14, 0, 0),
                                 ended_at=datetime(2026, 3, 20, 14, 0, 7))
        t2 = make_transcript_row(id=2, started_at=datetime(2026, 3, 20, 14, 1, 0),
                                 ended_at=datetime(2026, 3, 20, 14, 1, 5))
        t3 = make_transcript_row(id=3, started_at=datetime(2026, 3, 20, 14, 10, 0),
                                 ended_at=datetime(2026, 3, 20, 14, 10, 3))

        # Serialize datetimes like the real store does
        for t in [t1, t2, t3]:
            t["started_at"] = t["started_at"].isoformat()
            t["ended_at"] = t["ended_at"].isoformat()

        groups = ambient_extract.detect_conversation_boundaries([t1, t2, t3])
        assert len(groups) == 2
        assert len(groups[0]) == 2  # t1, t2 (1 minute apart)
        assert len(groups[1]) == 1  # t3 (9 minutes after t2)

    def test_single_transcript(self):
        t1 = make_transcript_row(id=1)
        t1["started_at"] = t1["started_at"].isoformat()
        t1["ended_at"] = t1["ended_at"].isoformat()

        groups = ambient_extract.detect_conversation_boundaries([t1])
        assert len(groups) == 1
        assert len(groups[0]) == 1

    def test_empty_list(self):
        assert ambient_extract.detect_conversation_boundaries([]) == []

    def test_all_within_gap(self):
        """All segments within gap → single group."""
        transcripts = []
        base = datetime(2026, 3, 20, 14, 0, 0)
        for i in range(5):
            t = make_transcript_row(id=i + 1,
                                    started_at=base + timedelta(minutes=i),
                                    ended_at=base + timedelta(minutes=i, seconds=7))
            t["started_at"] = t["started_at"].isoformat()
            t["ended_at"] = t["ended_at"].isoformat()
            transcripts.append(t)

        groups = ambient_extract.detect_conversation_boundaries(transcripts)
        assert len(groups) == 1
        assert len(groups[0]) == 5

    def test_custom_gap(self):
        t1 = make_transcript_row(id=1, started_at=datetime(2026, 3, 20, 14, 0, 0),
                                 ended_at=datetime(2026, 3, 20, 14, 0, 5))
        t2 = make_transcript_row(id=2, started_at=datetime(2026, 3, 20, 14, 3, 0),
                                 ended_at=datetime(2026, 3, 20, 14, 3, 5))
        for t in [t1, t2]:
            t["started_at"] = t["started_at"].isoformat()
            t["ended_at"] = t["ended_at"].isoformat()

        # With 2-minute gap, these should be in different groups
        groups = ambient_extract.detect_conversation_boundaries([t1, t2],
                                                                 gap_minutes=2.0)
        assert len(groups) == 2


# ---------------------------------------------------------------------------
# JSON parsing from CLI response
# ---------------------------------------------------------------------------

class TestParseJsonFromResponse:
    def test_clean_json(self):
        text = '{"commitments": [], "people": [], "topics": [], "summary": "Nothing notable"}'
        result = ambient_extract._parse_json_from_response(text)
        assert result is not None
        assert result["summary"] == "Nothing notable"

    def test_json_in_code_fence(self):
        text = '''Here's the extraction:
```json
{"commitments": [{"who": "self", "what": "call Mike"}], "people": [], "topics": ["phone call"], "summary": "Brief chat"}
```
'''
        result = ambient_extract._parse_json_from_response(text)
        assert result is not None
        assert len(result["commitments"]) == 1

    def test_json_with_prefix_text(self):
        text = 'Based on the transcript, here is the extraction:\n{"commitments": [], "people": [], "topics": [], "summary": "Quiet"}'
        result = ambient_extract._parse_json_from_response(text)
        assert result is not None

    def test_none_input(self):
        assert ambient_extract._parse_json_from_response(None) is None

    def test_empty_string(self):
        assert ambient_extract._parse_json_from_response("") is None

    def test_invalid_json(self):
        assert ambient_extract._parse_json_from_response("not json at all") is None


# ---------------------------------------------------------------------------
# Extraction via CLI (mocked)
# ---------------------------------------------------------------------------

class TestExtractFromBatch:
    @patch.object(ambient_extract, "_ask_cli")
    def test_successful_extraction(self, mock_cli):
        mock_cli.return_value = json.dumps({
            "commitments": [{"who": "self", "what": "finish proposal", "to_whom": "Mike", "due_date": "2026-03-22"}],
            "people": [{"name": "Mike", "relationship": "coworker", "organization": "Banker Wire"}],
            "topics": ["proposal", "Friday deadline"],
            "summary": "Discussed the proposal deadline with Mike",
        })

        transcripts = [
            {"id": 1, "started_at": "2026-03-20T14:00:00", "text": "So Mike, I'll have the proposal done by Friday",
             "speaker": None, "quality_speaker": None, "quality_text": None},
        ]
        result = ambient_extract.extract_from_batch(transcripts)
        assert result is not None
        assert len(result["commitments"]) == 1
        assert result["commitments"][0]["who"] == "self"
        assert len(result["people"]) == 1
        assert result["people"][0]["name"] == "Mike"
        mock_cli.assert_called_once()

    @patch.object(ambient_extract, "_ask_cli")
    def test_cli_failure_returns_none(self, mock_cli):
        mock_cli.return_value = None
        result = ambient_extract.extract_from_batch([
            {"id": 1, "started_at": "14:00:00", "text": "Hello",
             "speaker": None, "quality_speaker": None, "quality_text": None},
        ])
        assert result is None

    def test_empty_batch(self):
        assert ambient_extract.extract_from_batch([]) is None

    @patch.object(ambient_extract, "_ask_cli")
    def test_uses_quality_text_when_available(self, mock_cli):
        mock_cli.return_value = '{"commitments": [], "people": [], "topics": [], "summary": "test"}'
        transcripts = [
            {"id": 1, "started_at": "2026-03-20T14:00:00",
             "text": "raw text", "quality_text": "refined quality text",
             "speaker": "unknown", "quality_speaker": "Mike"},
        ]
        ambient_extract.extract_from_batch(transcripts)
        prompt = mock_cli.call_args[0][0]
        assert "refined quality text" in prompt
        assert "Mike" in prompt


# ---------------------------------------------------------------------------
# Store extraction results (mocked stores)
# ---------------------------------------------------------------------------

class TestStoreExtractionResults:
    @patch("ambient_extract.person_store")
    @patch("ambient_extract.commitment_store")
    def test_stores_commitments_and_people(self, mock_commit, mock_person):
        mock_commit.add.return_value = {"id": 1}
        mock_person.upsert.return_value = {"id": 1, "name": "Mike"}
        mock_person.record_mention.return_value = True

        extraction = {
            "commitments": [{"who": "self", "what": "call Mike", "to_whom": "Mike", "due_date": None}],
            "people": [{"name": "Mike", "relationship": "coworker", "organization": "Banker Wire"}],
        }
        transcripts = [{"id": 42}]
        ambient_extract.store_extraction_results(extraction, transcripts, conversation_id=1)

        mock_commit.add.assert_called_once()
        mock_person.upsert.assert_called_once_with(
            name="Mike", relationship="coworker", organization="Banker Wire",
        )
        mock_person.record_mention.assert_called_once_with("Mike")

    @patch("ambient_extract.person_store")
    @patch("ambient_extract.commitment_store")
    def test_skips_empty_names(self, mock_commit, mock_person):
        extraction = {"commitments": [], "people": [{"name": "", "relationship": None}]}
        ambient_extract.store_extraction_results(extraction, [{"id": 1}])
        mock_person.upsert.assert_not_called()

    def test_none_extraction(self):
        """Should not raise on None extraction."""
        ambient_extract.store_extraction_results(None, [])


# ---------------------------------------------------------------------------
# Extraction pass (full pipeline, mocked)
# ---------------------------------------------------------------------------

class TestRunExtractionPass:
    @patch.object(ambient_extract, "process_conversation_group")
    @patch("ambient_extract.ambient_store")
    def test_processes_and_marks_extracted(self, mock_store, mock_process):
        t1 = {"id": 1, "started_at": "2026-03-20T14:00:00",
               "ended_at": "2026-03-20T14:00:07"}
        t2 = {"id": 2, "started_at": "2026-03-20T14:01:00",
               "ended_at": "2026-03-20T14:01:05"}
        mock_store.get_unextracted.return_value = [t1, t2]
        mock_store.mark_extracted.return_value = 2
        mock_process.return_value = 1

        count = ambient_extract.run_extraction_pass()
        assert count == 2
        mock_store.mark_extracted.assert_called_once()
        mock_process.assert_called_once()

    @patch("ambient_extract.ambient_store")
    def test_no_unextracted(self, mock_store):
        mock_store.get_unextracted.return_value = []
        assert ambient_extract.run_extraction_pass() == 0


# ---------------------------------------------------------------------------
# Tick.py job functions
# ---------------------------------------------------------------------------

class TestTickJobs:
    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("ambient_extract.run_extraction_pass", return_value=5)
    def test_extraction_job_runs(self, mock_extract, mock_load, mock_save):
        import tick
        with patch.object(tick.config, "AMBIENT_ENABLED", True, create=True):
            tick.process_ambient_extraction()
            mock_extract.assert_called_once()

    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    def test_extraction_job_skips_when_disabled(self, mock_load, mock_save):
        import tick
        with patch.object(tick.config, "AMBIENT_ENABLED", False, create=True):
            tick.process_ambient_extraction()
            mock_save.assert_not_called()

    @patch("tick.save_state")
    @patch("tick.load_state", return_value={})
    @patch("ambient_audio.cleanup_old", return_value=(3, 1024000))
    @patch("ambient_store.cleanup_audio", return_value=3)
    def test_audio_cleanup_job(self, mock_store_cleanup, mock_file_cleanup,
                                mock_load, mock_save):
        import tick
        with patch.object(tick.config, "AMBIENT_ENABLED", True, create=True):
            tick.process_ambient_audio_cleanup()
            mock_file_cleanup.assert_called_once()
            mock_store_cleanup.assert_called_once()
