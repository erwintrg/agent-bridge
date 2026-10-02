"""The allow-list behind `GET <job> [param]` rows: instant, read-only data without starting an agent session.

Security model: no shell and no free-form commands. A job is a fixed argv list (or a built-in Python function).
The only value the cloud agent controls is one optional parameter, and it must fully match the job's regex.
Unknown job or bad parameter: the row is blocked and told which jobs exist. Keep this list read-only: anything that
writes, sends, deletes or pays belongs in a session, behind the approval gate."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Job:
    name: str
    argv: list[str] = field(default_factory=list)
    func: Callable[[str | None], str] | None = None
    param: re.Pattern | None = None       # None = the job takes no parameter
    default: str | None = None
    about: str = ""
    timeout: int = 600
    cwd: Path | None = None


class JobRegistry:
    def __init__(self):
        self.jobs: dict[str, Job] = {}

    def add(self, job: Job) -> None:
        self.jobs[job.name] = job

    def load_file(self, path: Path) -> None:
        """jobs.json: {"name": {"argv": [...], "param": "regex", "default": "7", "about": "..."}}.
        "{python}" becomes this interpreter, "{param}" the validated parameter; commands run in the file's folder."""
        path = Path(path)
        for name, spec in json.loads(path.read_text("utf-8")).items():
            if not re.fullmatch(r"[a-z0-9-]+", name):
                raise ValueError(f"bad job name {name!r} in {path}")
            argv = spec.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
                raise ValueError(f"job {name}: argv must be a non-empty list of strings")
            self.add(Job(name=name, argv=argv, param=re.compile(spec["param"]) if spec.get("param") else None,
                         default=spec.get("default"), about=spec.get("about", ""),
                         timeout=int(spec.get("timeout", 600)), cwd=path.parent))

    def help(self) -> str:
        return "known jobs: " + ", ".join(f"GET {n}" for n in sorted(self.jobs))

    def check(self, name: str, param: str | None) -> str | None:
        """None if the request is allowed, else the reason it is not."""
        job = self.jobs.get(name)
        if job is None:
            return f"unknown job '{name}'"
        if param is not None and (job.param is None or not job.param.fullmatch(param)):
            return f"bad parameter for '{name}'"
        return None

    def run(self, name: str, param: str | None) -> tuple[bool, str]:
        job = self.jobs[name]
        value = param if param is not None else job.default
        if job.func is not None:
            try:
                return True, job.func(value)
            except Exception as e:
                return False, f"{type(e).__name__}: {e}"
        argv = [a.replace("{python}", sys.executable).replace("{param}", value or "") for a in job.argv]
        argv = [a for a in argv if a != ""]
        try:
            r = subprocess.run(argv, cwd=job.cwd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=job.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {job.timeout} s"
        except OSError as e:
            return False, f"could not run: {e}"
        out, err = (r.stdout or "").strip(), (r.stderr or "").strip()
        if r.returncode != 0:
            # always carry the exit code: a bare "failed:" with no output once hid a wedged watcher for hours
            return False, f"exit {r.returncode}\n{(err or out)[:2000]}"
        return True, out or "(no output)"
