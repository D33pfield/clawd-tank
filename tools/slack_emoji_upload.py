#!/usr/bin/env python3
"""
Bulk-upload GIFs to a Slack workspace as custom emoji.

Calls Slack's internal emoji.add endpoint (same one the web UI uses).
Requires credentials extracted from a logged-in browser session:

  1. Open your Slack workspace in a browser and go to
     https://<workspace>.slack.com/customize/emoji
  2. Open DevTools -> Application -> Cookies -> slack.com
     Copy the value of the 'd' cookie (starts with 'xoxd-')
  3. On the same page, DevTools -> Sources -> (top frame) console, paste:
         JSON.parse(localStorage.localConfig_v2).teams[
           Object.keys(JSON.parse(localStorage.localConfig_v2).teams)[0]
         ].token
     Copy the resulting 'xoxc-...' token.
  4. Note your workspace subdomain (the '<workspace>' in the URL).

Usage:
    export SLACK_WORKSPACE=myteam
    export SLACK_XOXC=xoxc-...
    export SLACK_D_COOKIE=xoxd-...
    python tools/slack_emoji_upload.py assets/slack-emojis/

Flags:
    --prefix clawd-       Prepend a prefix to every emoji name
    --dry-run             Show what would be uploaded without sending
    --skip-existing       Skip emojis already present (default: true)
    --overwrite           Delete and re-upload if name already exists
"""

import argparse
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' is required. Install with: pip install requests", file=sys.stderr)
    sys.exit(1)


def emoji_name_from_path(path: Path, prefix: str) -> str:
    """Slack emoji names must be lowercase, alphanumeric + underscores + dashes."""
    stem = path.stem.lower()
    return f"{prefix}{stem}"


def list_existing_emoji(workspace: str, token: str, cookie: str) -> set[str]:
    url = f"https://{workspace}.slack.com/api/emoji.list"
    r = requests.post(
        url,
        data={"token": token},
        cookies={"d": cookie},
        timeout=30,
    )
    r.raise_for_status()
    js = r.json()
    if not js.get("ok"):
        raise RuntimeError(f"emoji.list failed: {js.get('error')} — {js}")
    return set(js.get("emoji", {}).keys())


def remove_emoji(workspace: str, token: str, cookie: str, name: str) -> None:
    url = f"https://{workspace}.slack.com/api/emoji.remove"
    r = requests.post(
        url,
        data={"token": token, "name": name},
        cookies={"d": cookie},
        timeout=30,
    )
    r.raise_for_status()
    js = r.json()
    if not js.get("ok"):
        raise RuntimeError(f"emoji.remove({name}) failed: {js.get('error')}")


def upload_emoji(workspace: str, token: str, cookie: str, name: str, path: Path) -> dict:
    url = f"https://{workspace}.slack.com/api/emoji.add"
    with open(path, "rb") as f:
        r = requests.post(
            url,
            data={"mode": "data", "name": name, "token": token},
            files={"image": (path.name, f, "image/gif")},
            cookies={"d": cookie},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description="Bulk upload GIFs as Slack custom emoji")
    ap.add_argument("input_dir", help="Directory of .gif files")
    ap.add_argument("--prefix", default="", help="Prefix for every emoji name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true",
                    help="Delete and re-upload if name already exists")
    ap.add_argument("--workspace", default=os.environ.get("SLACK_WORKSPACE"),
                    help="Workspace subdomain (or env SLACK_WORKSPACE)")
    ap.add_argument("--token", default=os.environ.get("SLACK_XOXC"),
                    help="xoxc token (or env SLACK_XOXC)")
    ap.add_argument("--cookie", default=os.environ.get("SLACK_D_COOKIE"),
                    help="d cookie value (or env SLACK_D_COOKIE)")
    args = ap.parse_args()

    if not args.dry_run:
        missing = [k for k, v in (("workspace", args.workspace),
                                  ("token", args.token),
                                  ("cookie", args.cookie)) if not v]
        if missing:
            print(f"Missing credentials: {', '.join(missing)}", file=sys.stderr)
            print("Set via flags or env vars (see --help)", file=sys.stderr)
            sys.exit(2)

    in_dir = Path(args.input_dir)
    gifs = sorted(in_dir.glob("*.gif"))
    if not gifs:
        print(f"No .gif files in {in_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(gifs)} GIF(s) in {in_dir}")

    existing: set[str] = set()
    if not args.dry_run:
        print("Fetching existing emoji list...")
        existing = list_existing_emoji(args.workspace, args.token, args.cookie)
        print(f"  Workspace has {len(existing)} existing emoji")

    uploaded = 0
    skipped = 0
    failed = 0

    for gif in gifs:
        name = emoji_name_from_path(gif, args.prefix)
        size_kb = gif.stat().st_size / 1024

        if args.dry_run:
            print(f"  DRY: :{name}:  ({size_kb:.1f} KB)")
            continue

        if name in existing:
            if args.overwrite:
                print(f"  :{name}: exists — removing first...")
                try:
                    remove_emoji(args.workspace, args.token, args.cookie, name)
                except Exception as e:  # noqa: BLE001
                    print(f"    remove failed: {e}")
                    failed += 1
                    continue
            else:
                print(f"  :{name}: already exists — skipping (use --overwrite to replace)")
                skipped += 1
                continue

        try:
            res = upload_emoji(args.workspace, args.token, args.cookie, name, gif)
        except Exception as e:  # noqa: BLE001
            print(f"  :{name}: ERROR {e}")
            failed += 1
            continue

        if res.get("ok"):
            print(f"  :{name}:  uploaded ({size_kb:.1f} KB)")
            uploaded += 1
        else:
            err = res.get("error", "unknown")
            print(f"  :{name}: FAILED — {err}")
            failed += 1
            # Rate-limited? Back off.
            if err == "ratelimited":
                time.sleep(5.0)

        # Gentle rate limit
        time.sleep(0.3)

    print()
    print(f"Uploaded: {uploaded}   Skipped: {skipped}   Failed: {failed}")


if __name__ == "__main__":
    main()
