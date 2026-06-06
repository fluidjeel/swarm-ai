#!/usr/bin/env python3
"""
Generate Fyers OAuth authorization URL locally (API v3).

Usage examples:
  python scripts/generate_fyers_auth_url.py --app-id "Y85JOJTQG5-100"
  python scripts/generate_fyers_auth_url.py --app-id "Y85JOJTQG5-100" --state "a2a-epic-1-1"
  python scripts/generate_fyers_auth_url.py --app-id "Y85JOJTQG5-100" --secret-key "PBFI9PTBM2"

You can also use env vars:
  FYERS_APP_ID, FYERS_SECRET_KEY, FYERS_REDIRECT_URI, FYERS_STATE
"""

from __future__ import annotations

import argparse
import os
from urllib.parse import urlencode


DEFAULT_REDIRECT_URI = "https://google.com"
FYERS_AUTH_ENDPOINT_V3 = "https://api-t1.fyers.in/api/v3/generate-authcode"


def build_auth_url(
    app_id: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    state: str = "a2a-local-auth",
    response_type: str = "code",
    app_id_param: str = "client_id",
) -> str:
    payload = {
        "redirect_uri": redirect_uri,
        "response_type": response_type,
        "state": state,
        "scope": "",
        "nonce": "",
    }
    payload[app_id_param] = app_id
    query = urlencode(payload)
    return f"{FYERS_AUTH_ENDPOINT_V3}?{query}"


def build_auth_url_sdk(
    app_id: str,
    secret_key: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    state: str = "a2a-local-auth",
) -> str:
    """
    Preferred generation route via official fyers-apiv3 SDK.
    """
    from fyers_apiv3 import fyersModel  # type: ignore

    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
        state=state,
    )
    return session.generate_authcode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Fyers auth URL")
    parser.add_argument(
        "--app-id",
        default=os.getenv("FYERS_APP_ID", ""),
        help="Fyers App ID (example: Y85JOJTQG5-100).",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("FYERS_SECRET_KEY", ""),
        help="Fyers Secret ID (optional, used for SDK-based URL generation).",
    )
    parser.add_argument(
        "--redirect-uri",
        default=os.getenv("FYERS_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        help=f"OAuth redirect URI (default: {DEFAULT_REDIRECT_URI}).",
    )
    parser.add_argument(
        "--state",
        default=os.getenv("FYERS_STATE", "a2a-local-auth"),
        help="CSRF state parameter (any opaque string).",
    )
    parser.add_argument(
        "--app-id-param",
        choices=["client_id", "appId"],
        default=os.getenv("FYERS_APP_ID_PARAM", "client_id"),
        help="Query param used for app id (default: client_id).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.app_id:
        print("ERROR: app id missing. Pass --app-id or set FYERS_APP_ID.")
        return 1

    # Prefer SDK-based generation when fyers-apiv3 + secret key are available.
    auth_url = ""
    if args.secret_key:
        try:
            auth_url = build_auth_url_sdk(
                app_id=args.app_id,
                secret_key=args.secret_key,
                redirect_uri=args.redirect_uri,
                state=args.state,
            )
        except Exception:
            auth_url = build_auth_url(
                app_id=args.app_id,
                redirect_uri=args.redirect_uri,
                state=args.state,
                app_id_param=args.app_id_param,
            )
    else:
        auth_url = build_auth_url(
            app_id=args.app_id,
            redirect_uri=args.redirect_uri,
            state=args.state,
            app_id_param=args.app_id_param,
        )

    print(auth_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
