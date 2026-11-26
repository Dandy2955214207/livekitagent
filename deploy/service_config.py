"""Create private deployment settings and administer trial invites (Python stdlib)."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import secrets
import time


def write_new(path, value, mode=0o600):
    with open(os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT, mode), "w",
              encoding="utf-8", newline="\n") as stream:
        stream.write(value)


def initialize(root):
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    write_new(root / ".gitignore", "*\n")
    access = root / "invites"
    access.mkdir()
    access.chmod(0o755)  # Readable by the non-root gateway through its directory bind mount.
    key, secret = secrets.token_hex(12), secrets.token_urlsafe(40)
    write_new(root / "livekit.yaml", f"port: 7880\nrtc:\n  tcp_port: 7881\n  udp_port: 7882\n"
              f"  use_external_ip: false\nkeys:\n  '{key}': '{secret}'\nlogging:\n  level: info\n")
    write_new(root / "session.env", f"LIVEKIT_URL=ws://livekit:7880\nLIVEKIT_API_KEY={key}\n"
              f"LIVEKIT_API_SECRET={secret}\nSERVICE_OWNER=livekitagent-{secrets.token_hex(12)}\n"
              "MODEL_PROVIDER=aliyun\nQWEN_LIVE_MEMORY_ENABLED=true\nQWEN_LIVE_PROACTIVE_ENABLED=true\n")
    write_new(root / "event.env", f"HARMONY_TRIAL_EVENT_KEY={secrets.token_hex(32)}\n")
    write_new(root / "provider.env", "QWEN_WORKSPACE_ID=\nQWEN_API_KEY=\n")
    write_new(root / "compose.env", f"SERVICE_CONFIG='{root.as_posix()}'\nGATEWAY_PORT=18095\n"
              "SERVICE_VERSION=local-verify\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    issue = commands.add_parser("issue")
    issue.add_argument("--hours", type=int, default=24, choices=range(1, 169), metavar="1..168")
    commands.add_parser("revoke").add_argument("id")
    args = parser.parse_args()
    root = args.directory.resolve()
    if args.command == "init":
        initialize(root)
    elif args.command == "issue":
        code = secrets.token_urlsafe(32)
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()
        target = root / "invites" / digest
        write_new(target, str(int(time.time()) + args.hours * 3600), 0o644)
        target.chmod(0o644)
        print(f"id={digest}\ncredential={code}")
    else:
        if not re.fullmatch(r"[0-9a-f]{64}", args.id):
            parser.error("invalid invite id")
        (root / "invites" / args.id).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
