#!/usr/bin/env python3
"""
Local Fyers API v3 authentication helper.

Flow:
  1. Generate auth URL (official SDK)
  2. Open browser for you to log in and approve the app
  3. Paste redirect URL (or auth_code) from the browser address bar
  4. Exchange auth_code for access token and save locally

Secrets are written to data/fyers_token.json (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from fyers_apiv3 import fyersModel
except ImportError:
    print("ERROR: pip install fyers-apiv3", file=sys.stderr)
    sys.exit(1)


ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = ROOT / "data" / "fyers_token.json"
DEFAULT_REDIRECT_URI = "https://google.com"


def extract_auth_code(raw: str) -> str:
    raw = raw.strip()
    if "auth_code=" in raw:
        parsed = urlparse(raw if "://" in raw else f"https://x/?{raw.lstrip('?')}")
        params = parse_qs(parsed.query)
        code = params.get("auth_code", [""])[0]
        if code:
            return code
    if re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        return raw
    raise ValueError("Could not parse auth_code. Paste full redirect URL or raw auth_code.")


def save_token(payload: dict) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    TOKEN_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"Saved token -> {TOKEN_PATH}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authenticate to Fyers API v3 locally")
    parser.add_argument("--app-id", default=os.getenv("FYERS_APP_ID", ""))
    parser.add_argument("--secret-key", default=os.getenv("FYERS_SECRET_KEY", ""))
    parser.add_argument(
        "--redirect-uri",
        default=os.getenv("FYERS_REDIRECT_URI", DEFAULT_REDIRECT_URI),
    )
    parser.add_argument("--state", default=os.getenv("FYERS_STATE", "a2a-local-auth"))
    parser.add_argument(
        "--auth-code",
        default=os.getenv("FYERS_AUTH_CODE", ""),
        help="Skip browser step if you already have auth_code.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Only print auth URL; do not open browser.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.app_id or not args.secret_key:
        print("ERROR: set --app-id and --secret-key (or FYERS_APP_ID / FYERS_SECRET_KEY).")
        return 1

    session = fyersModel.SessionModel(
        client_id=args.app_id,
        secret_key=args.secret_key,
        redirect_uri=args.redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=args.state,
    )

    auth_url = session.generate_authcode()
    print("Open this URL in your browser and approve the app:")
    print(auth_url)
    print()
    print("After login, copy the FULL redirect URL from the address bar.")
    print("It should look like:")
    print("  https://google.com/?auth_code=...&state=...")
    print()

    if not args.no_browser:
        webbrowser.open(auth_url, new=1)

    auth_code = args.auth_code
    if not auth_code:
        auth_code = input("Paste redirect URL or auth_code: ").strip()

    try:
        auth_code = extract_auth_code(auth_code)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") != "ok":
        print("ERROR: token exchange failed:")
        print(json.dumps(response, indent=2))
        return 1

    access_token = response.get("access_token", "")
    refresh_token = response.get("refresh_token", "")

    save_token(
        {
            "app_id": args.app_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "raw_response": response,
        }
    )

    print("[PASS] Fyers authentication successful.")
    print(f"Access token prefix: {access_token[:12]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
