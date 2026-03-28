#!/usr/bin/env python3
"""One-time Google OAuth2 PKCE authorization flow.

Run this once in a browser to get access + refresh tokens:
    python google_auth.py

It will:
1. Generate a PKCE code verifier/challenge
2. Print an authorization URL — open it in your browser
3. You authorize, get redirected to localhost (which will fail to load — that's fine)
4. Paste the full redirect URL back here
5. Exchanges the auth code for tokens and saves them to config.GOOGLE_TOKEN_FILE
"""

import base64
import hashlib
import json
import os
import sys
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

sys.path.insert(0, os.path.dirname(__file__))
import config


def generate_pkce():
    """Generate PKCE code verifier and challenge."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def main():
    verifier, challenge = generate_pkce()

    params = {
        "response_type": "code",
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "scope": " ".join(config.GOOGLE_SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }

    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    print("\n=== Google OAuth2 Authorization ===\n")
    print("Open this URL in your browser:\n")
    print(auth_url)
    print("\nAfter authorizing, you'll be redirected to a localhost URL that won't load.")
    print("That's expected. Copy the FULL URL from your browser's address bar and paste it here.\n")

    redirect_url = input("Paste the redirect URL here: ").strip()

    # Extract the authorization code
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)

    if "code" not in params:
        print("\nError: No authorization code found in URL.")
        if "error" in params:
            print(f"Error: {params['error'][0]}")
            if "error_description" in params:
                print(f"Description: {params['error_description'][0]}")
        sys.exit(1)

    code = params["code"][0]
    print(f"\nGot authorization code: {code[:8]}...")

    # Exchange code for tokens
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "code_verifier": verifier,
    }

    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if resp.status_code != 200:
        print(f"\nToken exchange failed ({resp.status_code}):")
        print(resp.text)
        sys.exit(1)

    tokens = resp.json()

    # Save tokens
    config.GOOGLE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_TOKEN_FILE.write_text(json.dumps(tokens, indent=2))

    print(f"\nTokens saved to {config.GOOGLE_TOKEN_FILE}")
    print(f"Scopes: {tokens.get('scope', 'unknown')}")
    print(f"Access token expires in: {tokens.get('expires_in', '?')} seconds")
    if "refresh_token" in tokens:
        print("Refresh token: received (will be used for automatic renewal)")
    else:
        print("WARNING: No refresh token received! Re-run with prompt=consent.")
    print("\nYou're all set! The daemon will auto-refresh tokens from here.")


if __name__ == "__main__":
    main()
