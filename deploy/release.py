"""Switch application image versions on one Compose installation during a maintenance window."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

DEPLOY = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("version", help="immutable local image tag, normally the Git revision")
    parser.add_argument("--project", required=True)
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", args.version):
        parser.error("invalid image tag")
    config = args.directory.resolve() / "compose.env"
    original = config.read_text(encoding="utf-8")
    versions = re.findall(r"^SERVICE_VERSION=([\w.-]+)$", original, re.M)
    if len(versions) != 1:
        parser.error("compose.env must contain one SERVICE_VERSION")
    previous = versions[0]
    base = ["docker", "compose", "--env-file", str(config), "-p", args.project, "-f", str(DEPLOY / "compose.yaml")]
    if args.fixture:
        base += ["-f", str(DEPLOY / "compose.fixture.yaml")]
    env = os.environ.copy()
    for name in ("SERVICE_CONFIG", "GATEWAY_PORT", "SERVICE_VERSION"):
        env.pop(name, None)
    subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}",
                    "livekitagent/service:" + args.version], check=True)
    existing = subprocess.check_output(base + ["ps", "--all", "-q"], env=env, text=True).strip()
    if existing:
        probe = ("import json,urllib.request; print(json.load(urllib.request.urlopen("
                 "'http://localhost:8000/api/health', timeout=5))['active_sessions'])")
        count = subprocess.check_output(base + ["exec", "-T", "api", "python", "-c", probe], env=env, text=True)
        if int(count):
            raise SystemExit("A call is active. Wait for hangup before changing the release.")

    def start(version):
        subprocess.run(base + ["up", "-d", "--wait", "--wait-timeout", "120"],
                       env={**env, "SERVICE_VERSION": version}, check=True)

    try:
        start(args.version)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config.parent,
                                         delete=False, newline="\n") as temp:
            temp.write(re.sub(r"^SERVICE_VERSION=[\w.-]+$", "SERVICE_VERSION=" + args.version, original, flags=re.M))
        os.replace(temp.name, config)
    except (OSError, subprocess.CalledProcessError, KeyboardInterrupt):
        if existing and previous != args.version:
            print("Restoring previously configured release", previous, flush=True)
            start(previous)
        raise
    print(json.dumps({"previous": previous, "current": args.version}))


if __name__ == "__main__":
    main()
