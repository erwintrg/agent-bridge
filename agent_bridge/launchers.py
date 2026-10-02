"""How a session turn is started.

`SystemdLauncher` is the production choice on Linux. A turn started as a plain child process of the watcher lives
in the watcher service's cgroup, and `systemctl --user restart` stops the whole cgroup: every deploy or restart
killed whatever session was mid-turn. `systemd-run --user --collect` gives each turn its own transient unit, so
restarting the watcher never touches running work, and the watcher finds the unit again by name.

`SubprocessLauncher` is the portable fallback (macOS, no systemd). `InlineLauncher` runs the turn in-process,
synchronously, for the demo and the tests."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _unit_safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name)


class InlineLauncher:
    def __init__(self, target: Callable[[list[str]], None]):
        self.target = target

    def launch(self, args: list[str], name: str) -> str:
        self.target(list(args))
        return f"inline:{name}"

    def alive(self, handle: str) -> bool:
        return False


class SubprocessLauncher:
    """Detached child process. Works anywhere, but under a systemd service it is still in the service's cgroup."""

    def __init__(self, env_file: Path | None, python: str = sys.executable):
        self.env_file, self.python = env_file, python
        self._procs: dict[int, subprocess.Popen] = {}

    def launch(self, args: list[str], name: str) -> str:
        env = dict(os.environ)
        if self.env_file:
            env["BRIDGE_ENV_FILE"] = str(self.env_file)
        p = subprocess.Popen([self.python, "-m", "agent_bridge", "turn", *args], cwd=PROJECT_ROOT, env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self._procs[p.pid] = p
        return f"pid:{p.pid}"

    def alive(self, handle: str) -> bool:
        if not handle.startswith("pid:"):
            return False
        pid = int(handle[4:])
        if pid in self._procs:                      # our own child: poll() also reaps it (no zombie)
            return self._procs[pid].poll() is None
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        try:                                        # a zombie still answers signal 0
            return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
        except OSError:
            return True


class SystemdLauncher:
    def __init__(self, env_file: Path | None, python: str = sys.executable, prefix: str = "agent-bridge"):
        self.env_file, self.python, self.prefix = env_file, python, prefix

    def launch(self, args: list[str], name: str) -> str:
        unit = _unit_safe(f"{self.prefix}-{name}-{int(time.time())}")
        cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
               f"--working-directory={PROJECT_ROOT}", "-p", "StandardOutput=null", "-p", "StandardError=null"]
        if self.env_file:   # a transient unit does not inherit the watcher's environment, only this file path
            cmd.append(f"--setenv=BRIDGE_ENV_FILE={self.env_file}")
        subprocess.run([*cmd, "--", self.python, "-m", "agent_bridge", "turn", *args], check=True, timeout=30)
        return f"unit:{unit}"

    def alive(self, handle: str) -> bool:
        if not handle.startswith("unit:"):
            return False
        r = subprocess.run(["systemctl", "--user", "is-active", handle[5:]], capture_output=True, text=True)
        return r.stdout.strip() in ("active", "activating", "reloading")
