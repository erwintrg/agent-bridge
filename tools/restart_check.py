#!/usr/bin/env python3
"""Reproduce the reason every turn runs as its own transient systemd unit (Linux with a systemd user session).

It runs the watcher as a user service, gives it one request, and stops the service while the turn is running,
the way a restart or a deploy would. The local agent is a fake `claude` that sleeps 12 s, so nothing real runs.

    python tools/restart_check.py               # both launchers, about 40 s
    python tools/restart_check.py systemd       # only one

Expected: with the systemd launcher the row ends `done`; with the subprocess launcher the turn dies together
with the watcher's cgroup and the row stays `in-progress`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAKE = """#!/usr/bin/env bash
sleep 12
echo '{"type":"result","result":"Done. (fake claude)","session_id":"fake-session-1","is_error":false}'
"""


def check(launcher: str) -> str:
    tmp = Path(tempfile.mkdtemp(prefix=f"agent-bridge-{launcher}-"))
    try:
        fake = tmp / "fake-claude.sh"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        env_file = tmp / ".env"
        env_file.write_text(f"BRIDGE_STATE_DIR=state\nBRIDGE_RUNNER=claude\nCLAUDE_BIN={fake}\n"
                            f"BRIDGE_LAUNCHER={launcher}\nPULSE_SYSTEM_CHECKS=0\n")

        def bridge(*args: str) -> str:
            return subprocess.run([sys.executable, "-m", "agent_bridge", "--env-file", str(env_file), *args],
                                  cwd=REPO, capture_output=True, text=True, check=True).stdout

        def status() -> str:
            rows = [json.loads(x) for x in bridge("board", "read").splitlines() if x.strip()]
            return rows[0]["status"] if rows else ""

        unit = f"agent-bridge-restart-check-{launcher}-{int(time.time())}"
        subprocess.run(["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}", f"--working-directory={REPO}",
                        "-p", "StandardOutput=null", "-p", "StandardError=null", "--", sys.executable, "-m",
                        "agent_bridge", "--env-file", str(env_file), "watch", "--interval", "2"], check=True)
        bridge("board", "add", "--owner", "PC", "--title", "A long task", "--by", "AGENT")
        for _ in range(40):
            if status() == "in-progress":
                break
            time.sleep(0.5)
        print(f"[{launcher}] turn running, row in-progress; stopping the watcher service now")
        subprocess.run(["systemctl", "--user", "stop", unit], check=True)
        time.sleep(16)
        final = status()
        print(f"[{launcher}] 16 s later the row is: {final}")
        return final
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if not shutil.which("systemd-run"):
        print("needs systemd (systemd-run --user)")
        return 2
    want = sys.argv[1:] or ["systemd", "subprocess"]
    results = {name: check(name) for name in want}
    ok = results.get("systemd", "done") == "done" and results.get("subprocess", "in-progress") == "in-progress"
    print("as expected" if ok else f"unexpected: {results}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
