"""Tests for context deduplication and broader keyword triggers.

Covers:
  - _dedup_tag() wrapping
  - _apply_context_dedup() hash-based dedup in session pool
  - Dedup hashes reset on session recycle
  - Broader keyword triggers (_MULTI_DOMAIN_SUBSTRINGS)
  - Context gap detection
"""

import re
from unittest.mock import patch, MagicMock

import pytest

from context import _dedup_tag
from session_pool import _apply_context_dedup, _DEDUP_RE


# ---------------------------------------------------------------------------
# _dedup_tag
# ---------------------------------------------------------------------------

class TestDedupTag:
    def test_wraps_content_with_tags(self):
        result = _dedup_tag("pantry", "Pantry data here")
        assert result.startswith("[dedup:pantry:")
        assert "Pantry data here" in result
        assert result.endswith("[/dedup:pantry]")

    def test_hash_changes_with_content(self):
        tag1 = _dedup_tag("pantry", "version A")
        tag2 = _dedup_tag("pantry", "version B")
        # Extract hashes
        h1 = tag1.split(":")[2].split("]")[0]
        h2 = tag2.split(":")[2].split("]")[0]
        assert h1 != h2

    def test_hash_stable_for_same_content(self):
        tag1 = _dedup_tag("x", "same content")
        tag2 = _dedup_tag("x", "same content")
        assert tag1 == tag2

    def test_regex_matches_tag(self):
        tagged = _dedup_tag("test_key", "some content")
        match = _DEDUP_RE.search(tagged)
        assert match is not None
        assert match.group(1) == "test_key"
        assert match.group(3).strip() == "some content"


# ---------------------------------------------------------------------------
# _apply_context_dedup
# ---------------------------------------------------------------------------

class TestApplyContextDedup:
    def test_first_injection_keeps_content(self):
        hashes = {}
        ctx = _dedup_tag("pantry", "Big pantry file content here")
        result = _apply_context_dedup(ctx, hashes)
        assert "Big pantry file content here" in result
        assert "pantry" in hashes  # hash cached

    def test_second_injection_same_content_deduped(self):
        hashes = {}
        ctx = _dedup_tag("pantry", "Same pantry content")
        # First injection
        _apply_context_dedup(ctx, hashes)
        # Second injection — same content
        result = _apply_context_dedup(ctx, hashes)
        assert "Same pantry content" not in result
        assert "unchanged from previous context" in result

    def test_changed_content_reinjected(self):
        hashes = {}
        ctx1 = _dedup_tag("health", "Calories: 500")
        ctx2 = _dedup_tag("health", "Calories: 1200")
        # First injection
        _apply_context_dedup(ctx1, hashes)
        # Second injection — content changed
        result = _apply_context_dedup(ctx2, hashes)
        assert "Calories: 1200" in result
        assert "unchanged" not in result

    def test_multiple_sections_independent(self):
        hashes = {}
        ctx = (
            "Some preamble\n"
            + _dedup_tag("pantry", "Pantry data")
            + "\n"
            + _dedup_tag("diet_ref", "Diet reference data")
        )
        # First injection — both full
        result = _apply_context_dedup(ctx, hashes)
        assert "Pantry data" in result
        assert "Diet reference data" in result

        # Change only diet_ref
        ctx2 = (
            "Some preamble\n"
            + _dedup_tag("pantry", "Pantry data")  # same
            + "\n"
            + _dedup_tag("diet_ref", "Updated diet reference")  # changed
        )
        result2 = _apply_context_dedup(ctx2, hashes)
        assert "Pantry data" not in result2  # deduped
        assert "pantry" in result2.lower() and "unchanged" in result2  # reference
        assert "Updated diet reference" in result2  # re-injected

    def test_no_tags_passes_through(self):
        hashes = {}
        ctx = "Plain context with no dedup tags"
        result = _apply_context_dedup(ctx, hashes)
        assert result == ctx

    def test_mixed_tagged_and_untagged(self):
        hashes = {}
        ctx = (
            "Current time: 2:30 PM\n"
            + _dedup_tag("pantry", "Pantry content")
            + "\nActive timers (1 total): ..."
        )
        result = _apply_context_dedup(ctx, hashes)
        assert "Current time: 2:30 PM" in result
        assert "Pantry content" in result
        assert "Active timers" in result


# ---------------------------------------------------------------------------
# Session recycle clears dedup hashes
# ---------------------------------------------------------------------------

class TestSessionRecycleClearsDedup:
    @pytest.mark.asyncio
    async def test_spawn_resets_dedup_hashes(self):
        from session_pool import _Session
        from unittest.mock import AsyncMock

        s = _Session("test", "max", 150)
        s._dedup_hashes = {"pantry": "abc123", "health": "def456"}

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 99999

        with patch("session_pool.asyncio.create_subprocess_exec",
                   new_callable=AsyncMock, return_value=mock_proc):
            await s._spawn()

        assert s._dedup_hashes == {}


# ---------------------------------------------------------------------------
# Broader keyword triggers
# ---------------------------------------------------------------------------

class TestBroaderKeywords:
    def test_what_did_i_eat_triggers_health(self):
        from context import _HEALTH_SUBSTRINGS
        assert any("what did i eat" in s for s in _HEALTH_SUBSTRINGS)

    def test_how_many_calories_triggers_health(self):
        from context import _HEALTH_SUBSTRINGS
        assert any("how many calories" in s for s in _HEALTH_SUBSTRINGS)

    def test_blood_pressure_triggers_health(self):
        from context import _HEALTH_SUBSTRINGS
        assert any("blood pressure" in s for s in _HEALTH_SUBSTRINGS)

    def test_multi_domain_triggers_exist(self):
        from context import _MULTI_DOMAIN_SUBSTRINGS
        assert "how am i doing" in _MULTI_DOMAIN_SUBSTRINGS
        assert "am i on track" in _MULTI_DOMAIN_SUBSTRINGS
        assert "catch me up" in _MULTI_DOMAIN_SUBSTRINGS


# ---------------------------------------------------------------------------
# Context gap detection
# ---------------------------------------------------------------------------

class TestContextGapDetection:
    def test_hedging_patterns_detected(self):
        from daemon import _CONTEXT_GAP_PATTERNS
        assert _CONTEXT_GAP_PATTERNS.search("I don't have access to that data")
        assert _CONTEXT_GAP_PATTERNS.search("I'm not sure when that happened")
        assert _CONTEXT_GAP_PATTERNS.search("I don't see any record of that")
        assert _CONTEXT_GAP_PATTERNS.search("I'd need to check the calendar")

    def test_normal_responses_not_flagged(self):
        from daemon import _CONTEXT_GAP_PATTERNS
        assert not _CONTEXT_GAP_PATTERNS.search("Your appointment is at 3pm")
        assert not _CONTEXT_GAP_PATTERNS.search("You ate 1,200 calories today")
        assert not _CONTEXT_GAP_PATTERNS.search("Sure, I set a timer for 30 minutes")
