"""Expiring tester credentials. Store only SHA-256 verifiers on disk."""

import hashlib
import hmac
from pathlib import Path
import re
import secrets
import time


class Invites:
    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise ValueError("Invite directory is missing")
        self.cookie_key = secrets.token_bytes(32)

    def active(self, digest):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False
        try:
            expires = int((self.directory / digest).read_text(encoding="ascii"))
        except (OSError, ValueError):
            return False
        return time.time() < expires

    def credential(self, value):
        digest = hashlib.sha256(value).hexdigest()
        return digest if self.active(digest) else ""

    def cookie(self, digest):
        signature = hmac.digest(self.cookie_key, digest.encode("ascii"), "sha256").hex()
        return digest + "." + signature

    def from_cookie(self, value):
        digest = value.partition(".")[0]
        if self.active(digest) and hmac.compare_digest(self.cookie(digest).encode(), value.encode()):
            return digest
        return ""
