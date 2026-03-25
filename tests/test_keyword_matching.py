"""Tests for keyword matching — verifying true positives and false positive reduction.

Tests the _match_keywords() helper and each category's refined keyword lists.
SAFETY: No external calls — pure string matching logic.
"""

import re

import pytest

from context import (
    _match_keywords,
    _WEATHER_SUBSTRINGS, _WEATHER_REGEX,
    _CALENDAR_SUBSTRINGS, _CALENDAR_REGEX,
    _HEALTH_SUBSTRINGS, _HEALTH_REGEX,
    _VEHICLE_SUBSTRINGS, _VEHICLE_REGEX,
    _LEGAL_SUBSTRINGS, _LEGAL_REGEX,
)


class TestMatchKeywordsHelper:
    def test_substring_match(self):
        assert _match_keywords("check the weather", ["weather"], None) is True

    def test_regex_match(self):
        pattern = re.compile(r'\b(rain)\b', re.IGNORECASE)
        assert _match_keywords("is it going to rain", [], pattern) is True

    def test_no_match(self):
        pattern = re.compile(r'\b(rain)\b', re.IGNORECASE)
        assert _match_keywords("hello world", ["weather"], pattern) is False

    def test_none_pattern(self):
        assert _match_keywords("some text", ["some"], None) is True
        assert _match_keywords("other text", ["some"], None) is False

    def test_case_insensitivity_regex(self):
        pattern = re.compile(r'\b(rain)\b', re.IGNORECASE)
        assert _match_keywords("RAIN today", [], pattern) is True


class TestWeatherKeywords:
    def _matches(self, text):
        return _match_keywords(text.lower().replace("-", " "),
                               _WEATHER_SUBSTRINGS, _WEATHER_REGEX)

    def test_true_positives(self):
        assert self._matches("What's the weather like?")
        assert self._matches("Is it going to rain tomorrow?")
        assert self._matches("Do I need an umbrella?")
        assert self._matches("What's the temperature?")
        assert self._matches("Snow forecast this week")
        assert self._matches("Will it freeze tonight?")
        assert self._matches("Wind advisory today")

    def test_false_positives_removed(self):
        assert not self._matches("I have a cold")
        assert not self._matches("She's hot")
        assert not self._matches("He's a warm person")
        assert not self._matches("Let's go outside")
        assert not self._matches("I want ice cream")
        assert not self._matches("The customer service was great")


class TestCalendarKeywords:
    def _matches(self, text):
        return _match_keywords(text.lower().replace("-", " "),
                               _CALENDAR_SUBSTRINGS, _CALENDAR_REGEX)

    def test_true_positives(self):
        assert self._matches("What's my schedule?")
        assert self._matches("Any appointments tomorrow?")
        assert self._matches("What's happening this week?")
        assert self._matches("Am I free Monday?")
        assert self._matches("Calendar for next week")

    def test_false_positives_removed(self):
        assert not self._matches("In the event that something happens")
        assert not self._matches("I plan to cook dinner")
        assert not self._matches("Free speech is important")
        assert not self._matches("This product is available everywhere")
        assert not self._matches("I feel weak today")


class TestHealthKeywords:
    def _matches(self, text):
        return _match_keywords(text.lower().replace("-", " "),
                               _HEALTH_SUBSTRINGS, _HEALTH_REGEX)

    def test_true_positives(self):
        assert self._matches("How's my heart rate?")
        assert self._matches("I have back pain")
        assert self._matches("What did I eat today?")
        assert self._matches("Log my lunch")
        assert self._matches("How many calories today?")
        assert self._matches("Sleep quality last night")
        assert self._matches("My weight this morning")
        assert self._matches("Check my fitbit data")
        assert self._matches("Track this meal")
        assert self._matches("Nutrition summary")
        assert self._matches("How many carbs today?")

    def test_false_positives_removed(self):
        assert not self._matches("Come back later")
        assert not self._matches("Go back home")
        assert not self._matches("Have a heart")
        assert not self._matches("Heart of the matter")
        assert not self._matches("Fat chance of that happening")
        assert not self._matches("Don't sugar coat it")
        assert not self._matches("Burn the midnight oil")
        assert not self._matches("Everybody knows that")
        assert not self._matches("She's very active on social media")

    def test_back_pain_still_triggers(self):
        """'back' removed but 'back pain' triggers via \\bpain\\b."""
        assert self._matches("My back pain is terrible")
        assert self._matches("Pain in my lower back")

    def test_heart_rate_still_triggers(self):
        """'heart' removed but 'heart rate' stays as substring."""
        assert self._matches("What's my heart rate?")
        assert self._matches("heart-rate is high")  # hyphen normalized to space

    def test_plurals_match(self):
        assert self._matches("How many meals today?")
        assert self._matches("Total carbs?")


class TestVehicleKeywords:
    def _matches(self, text):
        return _match_keywords(text.lower().replace("-", " "),
                               _VEHICLE_SUBSTRINGS, _VEHICLE_REGEX)

    def test_true_positives(self):
        assert self._matches("When's my next oil change?")
        assert self._matches("Xterra maintenance log")
        assert self._matches("Check tire pressure")
        assert self._matches("Brake pads need replacing")
        assert self._matches("Vehicle inspection due")

    def test_false_positives_removed(self):
        assert not self._matches("I need cooking oil")
        assert not self._matches("Get in the car")
        assert not self._matches("My car keys are missing")

    def test_truck_matches_with_boundary(self):
        assert self._matches("My truck needs work")
        assert not self._matches("I'm stuck")  # "truck" not in "stuck"


class TestLegalKeywords:
    def _matches(self, text):
        return _match_keywords(text.lower().replace("-", " "),
                               _LEGAL_SUBSTRINGS, _LEGAL_REGEX)

    def test_true_positives(self):
        assert self._matches("What's my next court date?")
        assert self._matches("Legal case update")
        assert self._matches("Call my lawyer")
        assert self._matches("Filing deadline")
        assert self._matches("Walworth county hearing")

    def test_specificity(self):
        # These are specific enough already — just verify they work
        assert self._matches("court case status")
        assert self._matches("attorney fees")
