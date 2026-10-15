#!/usr/bin/env python3
"""Add a Bark/ntfy recipient without putting secrets into command arguments."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import sys
import tempfile
import time
from urllib.parse import urlsplit, unquote


def private_write(path, content, owner):
    fd, temporary = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if owner:
            os.chown(temporary, owner.pw_uid, owner.pw_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--owner", help="Service account owning private files (root only)")
    parser.add_argument("--future-only", action="store_true", help="Skip already detected backlog for this recipient")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bark-url-stdin", action="store_true")
    mode.add_argument("--ntfy-topic-stdin", action="store_true")
    mode.add_argument("--wxpusher-spt-stdin", action="store_true")
    parser.add_argument("--ntfy-server", default="https://ntfy.sh")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.id):
        parser.error("Invalid recipient id")
    try:
        config_path = Path(args.config).resolve()
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        owner = pwd.getpwnam(args.owner) if args.owner else None
        raw = sys.stdin.read(4096).strip()
        if args.bark_url_stdin:
            # Accept either 'address and key' or a copied test curl command.
            match = re.search(r"https://[^\s'\"<>]+", raw)
            if not match:
                raise ValueError
            url = urlsplit(match[0])
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError
            pieces = [unquote(p) for p in url.path.split("/") if p]
            if not pieces or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", pieces[0]):
                raise ValueError
            key = pieces[0]
            secret_path = config_path.parent / (args.id + ".key")
            target = {"id": args.id, "type": "bark", "enabled": True,
                      "server": "https://" + url.netloc, "key_file": str(secret_path)}
        elif args.wxpusher_spt_stdin:
            match = re.search(r"SPT_[A-Za-z0-9_-]{8,128}", raw)
            if not match:
                raise ValueError
            key = match[0]
            secret_path = config_path.parent / (args.id + ".spt")
            target = {"id": args.id, "type": "wxpusher", "enabled": True,
                      "server": "https://wxpusher.zjiecode.com", "spt_file": str(secret_path)}
        else:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw):
                raise ValueError
            url = urlsplit(args.ntfy_server)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError
            key = raw
            secret_path = config_path.parent / (args.id + ".topic")
            target = {"id": args.id, "type": "ntfy", "enabled": True,
                      "server": args.ntfy_server.rstrip("/"), "topic_file": str(secret_path)}
        previous = next((t for t in cfg.get("targets", []) if t["id"] == args.id), None)
        if args.future_only:
            target["since"] = time.time()
        elif previous and "since" in previous:
            target["since"] = previous["since"]
        targets = [t for t in cfg.get("targets", []) if t["id"] != args.id]
        targets.append(target)
        cfg["targets"] = targets
        # Preserve the exact previous config. It contains file paths, not raw keys.
        backup = config_path.with_name("config.backup-" + str(time.time_ns()) + ".json")
        private_write(backup, config_path.read_text(encoding="utf-8"), owner)
        private_write(secret_path, key + "\n", owner)
        private_write(config_path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", owner)
        print("Configured recipient: " + args.id + "; credential hidden.")
        return 0
    except Exception:
        print("Configuration failed; check the input format and file permissions. Credential hidden.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
