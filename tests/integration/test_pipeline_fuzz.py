"""Hypothesis property-based fuzzing across the full ARIA pipeline.

Tests that random/adversarial inputs never crash core functions.
Uses real aria_test PostgreSQL database for DB-touching tests.

SAFETY: Uses aria_test database (integration conftest). No production DB access.
"""

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import actions
import nutrition_store
import training_store
import fitbit_store
import db


# ---------------------------------------------------------------------------
# ACTION block fuzzing
# ---------------------------------------------------------------------------

class TestActionBlockFuzz:
    """Hypothesis: random text through process_actions never crashes."""

    @given(text=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_text_never_crashes(self, text):
        """process_actions handles arbitrary text without exceptions."""
        result = actions.process_actions(text)
        assert isinstance(result, str)

    @given(text=st.text(min_size=0, max_size=5000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_text_strips_action_markers(self, text):
        """Output from process_actions never contains raw ACTION markers."""
        result = actions.process_actions(text)
        assert "<!--ACTION::" not in result

    @given(json_str=st.text(min_size=0, max_size=500))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_json_in_action_blocks(self, json_str):
        """Random JSON-like strings inside ACTION markers never crash."""
        text = f"Hello <!--ACTION::{json_str}--> world"
        result = actions.process_actions(text)
        assert isinstance(result, str)
        assert "<!--ACTION::" not in result

    def test_very_long_text(self):
        """50KB of random text does not crash process_actions."""
        long_text = "A" * 50_000
        result = actions.process_actions(long_text)
        assert isinstance(result, str)

    def test_only_whitespace(self):
        """Whitespace-only text does not crash process_actions."""
        for ws in ["", " ", "\t", "\n", "   \n\t  ", "\n" * 100]:
            result = actions.process_actions(ws)
            assert isinstance(result, str)

    def test_only_unicode(self):
        """Unicode-heavy text does not crash process_actions."""
        texts = [
            "\u0000\u0001\u0002",
            "\ud800".encode("utf-8", "replace").decode(),
            "\U0001f600\U0001f4a9\U0001f525" * 100,
            "\u200b\u200c\u200d\ufeff" * 500,
            "\u0410\u0411\u0412" * 1000,  # Cyrillic
            "\u4e00\u4e01\u4e02" * 1000,  # CJK
        ]
        for text in texts:
            result = actions.process_actions(text)
            assert isinstance(result, str)

    def test_malformed_partial_markers(self):
        """Partial ACTION markers do not crash."""
        partials = [
            "<!--ACTION::",
            "<!--ACTION::{}",
            "<!--ACTION::{}--><!--ACTION::",
            "<!--ACTION::-->",
            "<!--ACTION::{\"action\":\"unknown\"}-->",
            "<!--ACTION::{-->",
            "<!--ACTION::{\"bad-->",
        ]
        for text in partials:
            result = actions.process_actions(text)
            assert isinstance(result, str)

    def test_nested_markers(self):
        """Nested ACTION markers do not crash."""
        text = '<!--ACTION::{"action":"set_delivery","method":"voice"}--><!--ACTION::{"action":"set_delivery","method":"sms"}-->'
        result = actions.process_actions(text)
        assert isinstance(result, str)
        assert "<!--ACTION::" not in result

    def test_empty_json_in_marker(self):
        """Empty JSON inside ACTION marker does not crash."""
        text = "Hello <!--ACTION::{}-->"
        result = actions.process_actions(text)
        assert isinstance(result, str)

    @given(text=st.text(min_size=1, max_size=200))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_100_random_strings_no_crash(self, text):
        """100 random strings of various lengths never crash."""
        result = actions.process_actions(text)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Nutrition validation fuzzing
# ---------------------------------------------------------------------------

class TestNutritionValidationFuzz:
    """Hypothesis: random inputs to _validate_entry never crash."""

    @given(food_name=st.text(min_size=0, max_size=500))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_food_name(self, food_name):
        """Random food_name strings never crash _validate_entry."""
        result = nutrition_store._validate_entry(
            food_name, date.today().isoformat(), 1.0, {"calories": 100}
        )
        assert isinstance(result, list)

    @given(servings=st.floats(allow_nan=True, allow_infinity=True))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_servings(self, servings):
        """Random float servings never crash _validate_entry."""
        result = nutrition_store._validate_entry(
            "test food", date.today().isoformat(), servings, {"calories": 100}
        )
        assert isinstance(result, list)

    @given(nutrients=st.dictionaries(
        keys=st.text(min_size=1, max_size=50),
        values=st.one_of(
            st.integers(),
            st.floats(allow_nan=True, allow_infinity=True),
            st.text(min_size=0, max_size=20),
            st.none(),
        ),
        max_size=20,
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_nutrient_dicts(self, nutrients):
        """Random nutrient dicts with random keys/values never crash."""
        result = nutrition_store._validate_entry(
            "test food", date.today().isoformat(), 1.0, nutrients
        )
        assert isinstance(result, list)

    @given(d=st.dates(
        min_value=date(2020, 1, 1),
        max_value=date(2030, 12, 31),
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_valid_dates(self, d):
        """Random valid ISO dates never crash _validate_entry."""
        result = nutrition_store._validate_entry(
            "test food", d.isoformat(), 1.0, {"calories": 100}
        )
        assert isinstance(result, list)

    @given(d=st.text(min_size=0, max_size=30))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_invalid_dates(self, d):
        """Random invalid date strings return a list with errors (no crash)."""
        result = nutrition_store._validate_entry(
            "test food", d, 1.0, {"calories": 100}
        )
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Entity extraction fuzzing
# ---------------------------------------------------------------------------

class TestEntityExtractionFuzz:
    """Hypothesis: random text through extract_entities never crashes."""

    @given(text=st.text(min_size=0, max_size=2000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_random_text_returns_list_of_tuples(self, text):
        """extract_entities always returns a list of 3-tuples."""
        result = training_store.extract_entities(text, source="test")
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 3

    @given(text=st.text(min_size=0, max_size=2000))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_tuple_types_are_str(self, text):
        """Each tuple from extract_entities has (str, str, str) types."""
        result = training_store.extract_entities(text, source="test")
        for entity_type, entity_value, snippet in result:
            assert isinstance(entity_type, str)
            assert isinstance(entity_value, str)
            assert isinstance(snippet, str)

    def test_very_long_text_no_hang(self):
        """100KB of text does not hang or crash extract_entities."""
        long_text = "John Smith went to the store. " * 3334  # ~100KB
        result = training_store.extract_entities(long_text, source="test")
        assert isinstance(result, list)

    def test_only_special_characters(self):
        """Text with only special characters does not crash."""
        specials = [
            "!@#$%^&*()_+-=[]{}|;':\",./<>?",
            "\x00\x01\x02\x03\x04",
            "\n\r\t" * 100,
            "<<<>>>&&&&||||",
            "<!---->",
        ]
        for text in specials:
            result = training_store.extract_entities(text, source="test")
            assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Fitbit data shape fuzzing
# ---------------------------------------------------------------------------

class TestFitbitDataShapeFuzz:
    """Hypothesis: Fitbit safe cast functions handle any input."""

    @given(val=st.one_of(
        st.integers(min_value=-2**31, max_value=2**31),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(min_size=0, max_size=50),
        st.none(),
        st.booleans(),
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_safe_int_always_returns_int(self, val):
        """_safe_int with realistic API input always returns an int."""
        result = fitbit_store._safe_int(val)
        assert isinstance(result, int)

    @given(val=st.one_of(
        st.integers(min_value=-2**31, max_value=2**31),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(min_size=0, max_size=50),
        st.none(),
        st.booleans(),
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_safe_float_always_returns_float(self, val):
        """_safe_float with random input always returns a float."""
        result = fitbit_store._safe_float(val)
        assert isinstance(result, float)

    @given(data=st.dictionaries(
        keys=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
            min_size=1, max_size=30,
        ),
        values=st.one_of(st.integers(), st.none(), st.booleans()),
        max_size=15,
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_random_activity_shape(self, data):
        """Random JSONB shapes through get_activity_summary return dict or None."""
        from tests.integration.conftest import seed_fitbit_snapshot
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"activity": data})
        result = fitbit_store.get_activity_summary(today)
        assert result is None or isinstance(result, dict)

    @given(data=st.dictionaries(
        keys=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
            min_size=1, max_size=30,
        ),
        values=st.one_of(
            st.integers(), st.none(),
            st.dictionaries(
                keys=st.text(
                    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
                    min_size=1, max_size=20,
                ),
                values=st.one_of(st.integers(), st.none()),
                max_size=5,
            ),
        ),
        max_size=10,
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_random_sleep_shape(self, data):
        """Random JSONB shapes through get_sleep_summary return dict or None."""
        from tests.integration.conftest import seed_fitbit_snapshot
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"sleep": data})
        result = fitbit_store.get_sleep_summary(today)
        assert result is None or isinstance(result, dict)

    @given(data=st.dictionaries(
        keys=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
            min_size=1, max_size=30,
        ),
        values=st.one_of(
            st.integers(), st.none(),
            st.dictionaries(
                keys=st.text(
                    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
                    min_size=1, max_size=20,
                ),
                values=st.one_of(st.integers(), st.none()),
                max_size=5,
            ),
        ),
        max_size=10,
    ))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_random_heart_shape(self, data):
        """Random JSONB shapes through get_heart_summary return dict or None."""
        from tests.integration.conftest import seed_fitbit_snapshot
        today = date.today().isoformat()
        seed_fitbit_snapshot(today, {"heart_rate": data})
        result = fitbit_store.get_heart_summary(today)
        assert result is None or isinstance(result, dict)
