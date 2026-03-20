"""Fuzz and property-based tests for process_actions() — ACTION block parsing.

Uses hypothesis to generate random inputs and verify the parser never crashes
and always strips ACTION blocks from output.

Install: pip install hypothesis
"""

import json
import re
from unittest.mock import patch, MagicMock

import pytest

try:
    from hypothesis import given, strategies as st, settings, assume
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

import daemon


pytestmark = pytest.mark.skipif(not HAS_HYPOTHESIS, reason="hypothesis not installed")


class TestProcessActionsNeverCrashes:
    @given(st.text(max_size=5000))
    @settings(max_examples=200)
    def test_arbitrary_text(self, text):
        """process_actions must never raise, regardless of input."""
        with patch("daemon.calendar_store", MagicMock()), \
             patch("daemon.vehicle_store", MagicMock()), \
             patch("daemon.health_store", MagicMock()), \
             patch("daemon.legal_store", MagicMock()), \
             patch("daemon.timer_store", MagicMock()), \
             patch("daemon.nutrition_store", MagicMock()), \
             patch("daemon.fitbit_store", MagicMock()), \
             patch("daemon.log_request"):
            result = daemon.process_actions(text)
            assert isinstance(result, str)

    @given(st.text(min_size=1, max_size=200))
    @settings(max_examples=100)
    def test_action_markers_always_stripped(self, inner):
        """Even with random content inside ACTION markers, they get stripped."""
        text = f"before <!--ACTION::{inner}--> after"
        with patch("daemon.calendar_store", MagicMock()), \
             patch("daemon.log_request"):
            result = daemon.process_actions(text)
            assert "<!--ACTION::" not in result


class TestMalformedActionBlocks:
    @pytest.mark.parametrize("block", [
        '<!--ACTION::{}-->',
        '<!--ACTION::{"action": null}-->',
        '<!--ACTION::{"action": ""}-->',
        '<!--ACTION::{"no_action_key": true}-->',
        '<!--ACTION::[]-->',
        '<!--ACTION::"just a string"-->',
        '<!--ACTION::42-->',
        '<!--ACTION::true-->',
        '<!--ACTION::{"action": "add_event"}-->',  # missing required fields
    ])
    def test_malformed_blocks_dont_crash(self, block):
        with patch("daemon.calendar_store", MagicMock()), \
             patch("daemon.log_request"):
            result = daemon.process_actions(f"Text {block}")
            assert isinstance(result, str)
            assert "<!--ACTION" not in result


class TestActionBlockEdgeCases:
    def test_nested_json(self):
        """ACTION block with deeply nested JSON."""
        action = {
            "action": "log_nutrition",
            "food_name": "Test",
            "nutrients": {"calories": 100, "nested": {"deep": True}},
        }
        text = f'OK <!--ACTION::{json.dumps(action)}-->'
        with patch("daemon.nutrition_store") as mock_ns:
            daemon.process_actions(text)
            mock_ns.add_item.assert_called_once()

    def test_unicode_in_action(self):
        """ACTION block with unicode characters."""
        action = {
            "action": "add_event",
            "title": "Caf\u00e9 meeting \u2615",
            "date": "2026-03-20",
        }
        text = f'Done! <!--ACTION::{json.dumps(action)}-->'
        with patch("daemon.calendar_store") as mock_cal:
            daemon.process_actions(text)
            mock_cal.add_event.assert_called_once()
            assert "Caf\u00e9" in mock_cal.add_event.call_args[1]["title"]

    def test_action_block_with_newlines(self):
        """ACTION block JSON may span multiple lines."""
        action_json = '{"action": "add_event",\n"title": "Test",\n"date": "2026-03-20"}'
        text = f'Done! <!--ACTION::{action_json}-->'
        with patch("daemon.calendar_store") as mock_cal:
            daemon.process_actions(text)
            mock_cal.add_event.assert_called_once()

    def test_many_action_blocks(self):
        """Response with many ACTION blocks."""
        actions = []
        for i in range(10):
            actions.append(
                f'<!--ACTION::{{"action": "log_health", "date": "2026-03-20", '
                f'"category": "general", "description": "item {i}"}}-->'
            )
        text = "Logged everything! " + " ".join(actions)
        with patch("daemon.health_store") as mock_hs:
            result = daemon.process_actions(text)
            assert mock_hs.add_entry.call_count == 10
            assert "<!--ACTION" not in result


class TestRegexEdgeCases:
    def test_partial_action_marker(self):
        """Text containing partial ACTION-like patterns."""
        text = "The <!--ACTION tag is incomplete"
        result = daemon.process_actions(text)
        assert result == text  # unchanged

    def test_double_close_marker(self):
        text = 'Test <!--ACTION::{"action": "add_event", "title": "X", "date": "2026-03-20"}-->-->'
        with patch("daemon.calendar_store") as mock_cal:
            result = daemon.process_actions(text)
            # Should parse the action and leave the trailing -->
            mock_cal.add_event.assert_called_once()

    def test_html_comment_that_looks_like_action(self):
        """Regular HTML comments should not be parsed as actions."""
        text = "<!-- This is a comment --> and <!--not an action-->"
        result = daemon.process_actions(text)
        assert result == text  # no ACTION:: prefix, no match


class TestClaimDetectionFuzz:
    @given(st.sampled_from([
        "I logged your symptoms.",
        "I've saved your event.",
        "I tracked your nutrition.",
    ]))
    def test_first_person_claims_trigger(self, text):
        """First-person claim phrases should always trigger the warning."""
        result = daemon.process_actions(text)
        assert "System note" in result

    @given(st.sampled_from([
        "The data was stored in the cloud by the vendor",
        "She noted the address in her book",
        "meals logged 3 of last 7 days",
        "No meals logged today",
        "calories tracked this week",
    ]))
    def test_descriptive_text_no_trigger(self, text):
        """Descriptive/third-person text should NOT trigger the warning."""
        result = daemon.process_actions(text)
        assert "System note" not in result
