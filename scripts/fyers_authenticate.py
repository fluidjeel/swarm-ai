#!/usr/bin/env python3
"""
Local Fyers API v3 authentication helper.

Flow:
  1. Generate auth URL (official SDK)
  2. Open browser for you to log in and approve the app
  3. Paste redirect URL (or auth_code) from the browser address bar
  4. Exchange auth_code for access token and save locally
  5. Sync FYERS_APP_ID + FYERS_ACCESS_TOKEN to local .env, AWS SSM, and
     optionally EC2 .env over SSH (laptop-only browser auth).

Secrets are written to data/fyers_token.json (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
ENV_PATH = ROOT / ".env"
DEFAULT_REDIRECT_URI = "https://google.com"
FYERS_SYNC_KEYS = ("FYERS_APP_ID", "FYERS_ACCESS_TOKEN")

_EC2_REMOTE_ENV_SCRIPT = r"""
import json
import sys
from pathlib import Path

updates = json.loads(sys.stdin.read())
env_path = Path(sys.argv[1]).expanduser()
lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
remaining = []
seen = set()
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        remaining.append(line)
        continue
    key = line.split("=", 1)[0].strip()
    if key in updates:
        seen.add(key)
        continue
    remaining.append(line)
if remaining and remaining[-1].strip():
    remaining.append("")
if not any(line.strip() == "# Fyers API v3" for line in remaining):
    remaining.extend(["", "# Fyers API v3"])
for key in sorted(updates):
    remaining.append(f"{key}={updates[key]}")
env_path.parent.mkdir(parents=True, exist_ok=True)
env_path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
print(f"Updated {env_path}")
"""


def _load_local_env() -> None:
    """Load gitignored .env before reading FYERS_* os.environ defaults."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from src.config.secrets import load_project_env

        load_project_env()
    except ImportError:
        env_path = ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


def _default_app_id() -> str:
    env_value = os.getenv("FYERS_APP_ID", "").strip()
    if env_value:
        return env_value
    if not TOKEN_PATH.exists():
        return ""
    try:
        payload = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("app_id", "")).strip()


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


def upsert_env_file(path: Path, updates: dict[str, str]) -> None:
    """Insert or replace KEY=VALUE lines in a dotenv file."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            remaining.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            continue
        remaining.append(line)

    if remaining and remaining[-1].strip():
        remaining.append("")
    if not any(line.strip() == "# Fyers API v3" for line in remaining):
        remaining.extend(["", "# Fyers API v3"])
    for key in FYERS_SYNC_KEYS:
        if key in updates:
            remaining.append(f"{key}={updates[key]}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    print(f"Updated local env -> {path}")


def sync_fyers_to_ssm(
    updates: dict[str, str],
    *,
    prefix: str,
    region: str,
    dry_run: bool,
) -> bool:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.config.secrets import ssm_parameter_name

    if dry_run:
        print(f"DRY RUN SSM prefix: {prefix} region: {region}")
        for key in FYERS_SYNC_KEYS:
            if key in updates:
                print(f"  would upsert {ssm_parameter_name(key, prefix)}")
        return True

    try:
        import boto3
    except ImportError:
        print("WARN: boto3 not installed; skipped SSM sync.")
        return False

    client = boto3.client("ssm", region_name=region)
    for key in FYERS_SYNC_KEYS:
        value = updates.get(key, "").strip()
        if not value:
            continue
        name = ssm_parameter_name(key, prefix)
        client.put_parameter(
            Name=name,
            Value=value,
            Type="SecureString",
            Overwrite=True,
            Description=f"A2A Trading Engine {key}",
        )
        print(f"Upserted SSM parameter -> {name}")
    return True


def sync_fyers_to_ec2_ssh(
    updates: dict[str, str],
    *,
    ssh_target: str,
    remote_env_path: str,
    dry_run: bool,
) -> bool:
    if dry_run:
        print(f"DRY RUN EC2 SSH target: {ssh_target}")
        print(f"  remote env: {remote_env_path}")
        for key in FYERS_SYNC_KEYS:
            if key in updates:
                print(f"  would set {key}")
        return True

    payload = json.dumps({key: updates[key] for key in FYERS_SYNC_KEYS if key in updates})
    try:
        result = subprocess.run(
            ["ssh", ssh_target, "python3", "-c", _EC2_REMOTE_ENV_SCRIPT, remote_env_path],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        print("WARN: ssh client not found; skipped EC2 .env sync.")
        return False

    if result.returncode != 0:
        print("WARN: EC2 SSH env sync failed.")
        if result.stderr.strip():
            print(result.stderr.strip())
        return False

    if result.stdout.strip():
        print(result.stdout.strip())
    print(f"Synced Fyers credentials to EC2 via SSH ({ssh_target})")
    return True


def sync_credentials(
    *,
    app_id: str,
    access_token: str,
    sync_local: bool,
    sync_ssm: bool,
    sync_ec2_ssh: str | None,
    ec2_env_path: str,
    ssm_prefix: str,
    aws_region: str,
    dry_run: bool,
) -> None:
    updates = {
        "FYERS_APP_ID": app_id,
        "FYERS_ACCESS_TOKEN": access_token,
    }

    if sync_local:
        if dry_run:
            print(f"DRY RUN local env -> {ENV_PATH}")
        else:
            upsert_env_file(ENV_PATH, updates)

    if sync_ssm:
        ok = sync_fyers_to_ssm(
            updates,
            prefix=ssm_prefix,
            region=aws_region,
            dry_run=dry_run,
        )
        if not ok and not dry_run:
            print("WARN: SSM sync skipped or failed. EC2 may still use stale credentials.")

    if sync_ec2_ssh:
        sync_fyers_to_ec2_ssh(
            updates,
            ssh_target=sync_ec2_ssh,
            remote_env_path=ec2_env_path,
            dry_run=dry_run,
        )


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
    parser.add_argument(
        "--no-sync-local",
        action="store_true",
        help="Do not update local .env after successful auth.",
    )
    parser.add_argument(
        "--no-sync-ssm",
        action="store_true",
        help="Do not push FYERS_APP_ID / FYERS_ACCESS_TOKEN to AWS SSM.",
    )
    parser.add_argument(
        "--sync-ec2-ssh",
        nargs="?",
        const=os.getenv("A2A_EC2_SSH_TARGET", ""),
        default=None,
        metavar="USER@HOST",
        help=(
            "Update EC2 ~/swarm-ai/.env over SSH. "
            "Uses A2A_EC2_SSH_TARGET when flag is passed without a value."
        ),
    )
    parser.add_argument(
        "--ec2-env-path",
        default=os.getenv("A2A_EC2_ENV_PATH", "~/swarm-ai/.env"),
        help="Remote dotenv path on EC2 (default: ~/swarm-ai/.env).",
    )
    parser.add_argument(
        "--ssm-prefix",
        default=os.getenv("A2A_SSM_PARAM_PREFIX", "/a2a/llm"),
        help="SSM prefix for FYERS_* SecureString parameters.",
    )
    parser.add_argument(
        "--aws-region",
        default=os.getenv("AWS_REGION", "ap-south-1"),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print sync actions without writing .env, SSM, or EC2 files.",
    )
    return parser.parse_args()


def main() -> int:
    _load_local_env()
    args = parse_args()
    if not args.app_id:
        args.app_id = _default_app_id()
    if not args.app_id or not args.secret_key:
        print("ERROR: set --app-id and --secret-key (or FYERS_APP_ID / FYERS_SECRET_KEY).")
        print("Add FYERS_SECRET_KEY to .env (Fyers dashboard -> My Apps -> App Secret).")
        print(f"FYERS_APP_ID is {'set' if args.app_id else 'missing'}.")
        print(f"FYERS_SECRET_KEY is {'set' if args.secret_key else 'missing'}.")
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

    if not args.dry_run:
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

    ec2_ssh_target = (args.sync_ec2_ssh or "").strip() or None
    sync_credentials(
        app_id=args.app_id,
        access_token=access_token,
        sync_local=not args.no_sync_local,
        sync_ssm=not args.no_sync_ssm,
        sync_ec2_ssh=ec2_ssh_target,
        ec2_env_path=args.ec2_env_path,
        ssm_prefix=args.ssm_prefix,
        aws_region=args.aws_region,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
