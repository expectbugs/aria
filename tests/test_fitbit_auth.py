"""Tests for fitbit_auth.py — OAuth2 PKCE flow.

SAFETY: No actual Fitbit OAuth requests are made.
"""

import base64
import hashlib

import fitbit_auth


class TestPKCEGeneration:
    def test_generates_verifier_and_challenge(self):
        verifier, challenge = fitbit_auth.generate_pkce()
        assert len(verifier) > 20
        assert len(challenge) > 20
        assert verifier != challenge

    def test_challenge_matches_verifier(self):
        verifier, challenge = fitbit_auth.generate_pkce()
        # Recompute challenge from verifier
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert challenge == expected

    def test_different_each_call(self):
        v1, c1 = fitbit_auth.generate_pkce()
        v2, c2 = fitbit_auth.generate_pkce()
        assert v1 != v2
        assert c1 != c2
