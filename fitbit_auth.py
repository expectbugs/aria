#!/usr/bin/env python3
"""One-time Fitbit OAuth2 PKCE authorization flow.

Run this once in a browser to get access + refresh tokens:
    python fitbit_auth.py

It will:
1. Generate a PKCE code verifier/challenge
2. Print an authorization URL — open it in your browser
3. You authorize, get redirected to localhost (which will fail to load — that's fine)
4. Paste the full redirect URL back here
5. Exchanges the auth code for tokens and saves them to config.FITBIT_TOKEN_FILE
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
        "client_id": config.FITBIT_CLIENT_ID,
        "redirect_uri": config.FITBIT_REDIRECT_URI,
        "scope": " ".join(config.FITBIT_SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    auth_url = f"https://www.fitbit.com/oauth2/authorize?{urlencode(params)}"

    print("\n=== Fitbit OAuth2 Authorization ===\n")
    print("Open this URL in your browser:\n")
    print(auth_url)
    print("\nAfter authorizing, you'll be redirected to a localhost URL that won't load.")
    print("That's expected. Copy the FULL URL from your browser's address bar and paste it here.\n")

    redirect_url = input("Paste the redirect URL here: ").strip()

    # Extract the authorization code
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)

    if "code" not in params:
        print(f"\nError: No authorization code found in URL.")
        if "error" in params:
            print(f"Error: {params['error'][0]}")
            if "error_description" in params:
                print(f"Description: {params['error_description'][0]}")
        sys.exit(1)

    code = params["code"][0]
    print(f"\nGot authorization code: {code[:8]}...")

    # Exchange code for tokens
    token_data = {
        "client_id": config.FITBIT_CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.FITBIT_REDIRECT_URI,
        "code_verifier": verifier,
    }

    # Basic auth header with client_id:client_secret
    auth = (config.FITBIT_CLIENT_ID, config.FITBIT_CLIENT_SECRET)

    resp = httpx.post(
        "https://api.fitbit.com/oauth2/token",
        data=token_data,
        auth=auth,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if resp.status_code != 200:
        print(f"\nToken exchange failed ({resp.status_code}):")
        print(resp.text)
        sys.exit(1)

    tokens = resp.json()

    # Save tokens
    config.FITBIT_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.FITBIT_TOKEN_FILE.write_text(json.dumps(tokens, indent=2))

    print(f"\nTokens saved to {config.FITBIT_TOKEN_FILE}")
    print(f"User ID: {tokens.get('user_id', 'unknown')}")
    print(f"Scopes: {tokens.get('scope', 'unknown')}")
    print(f"Access token expires in: {tokens.get('expires_in', '?')} seconds")
    print("\nYou're all set! The daemon will auto-refresh tokens from here.")


if __name__ == "__main__":
    main()
