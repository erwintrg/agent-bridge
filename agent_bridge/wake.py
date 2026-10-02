"""Wake-ups for the cloud agent.

The cloud agent cannot be called directly; it runs when something triggers its routine. In production that trigger
is a GitHub pull request in a private repo: the routine fires on "PR opened", reads the board, acts, and closes the
PR unmerged. A wake-up carries no data of its own. It only says "look at the board", so several wake-ups can be
merged into one without losing anything: the board is the source of truth.

`RateLimitedWaker` allows one wake-up per gap (default 2 min). Anything inside the gap is queued and the watcher's
`flush()` sends the whole queue as ONE wake-up once the gap has passed. A failed send is queued again, never lost."""
from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

from .clock import Clock
from .util import file_lock, one_line


class LogWaker:
    """Offline backend: append each wake-up to a log file (the demo prints it)."""

    def __init__(self, path: Path, clock: Clock, agent_name: str):
        self.path, self.clock, self.agent = Path(path), clock, agent_name

    def send(self, reason: str, sender: str) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(f"{self.clock.stamp()}  WAKE {self.agent} <- {sender}: {reason}\n")
        return f"sent (logged to {self.path.name})"


class GitHubPRWaker:
    """One wake-up = a branch wake/<stamp> with one note file + a PR labelled `wake`. Old closed wake branches are
    deleted on the next wake-up. Uses the `gh` CLI, so no token lives in this code or its config."""

    def __init__(self, repo: str, base: str, agent_name: str, clock: Clock, gh: str = "gh"):
        if not repo:
            raise ValueError("WAKE_GITHUB_REPO is not set")
        self.repo, self.base, self.agent, self.clock, self.gh = repo, base, agent_name, clock, gh

    def _api(self, *args: str):
        r = subprocess.run([self.gh, "api", *args], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:200] or f"gh api exit {r.returncode}")
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def note(self, reason: str, sender: str) -> str:
        return (f"# WAKE {self.clock.stamp()}\n\nFrom: {sender}\n\n{reason}\n\n"
                f"Read the task board (rows for you or your team, status open, and rows you requested that changed) "
                f"and any shared files named above, act on them, write results back to the board, then close this PR "
                f"WITHOUT merging. Nothing outward without the user's approval.\n")

    def send(self, reason: str, sender: str) -> str:
        stamp = self.clock.dt().strftime("%Y%m%d-%H%M%S")
        branch = f"wake/{stamp}"
        for pr in self._api(f"repos/{self.repo}/pulls?state=closed&per_page=30"):
            ref = (pr.get("head") or {}).get("ref", "")
            if ref.startswith("wake/"):
                subprocess.run([self.gh, "api", "-X", "DELETE", f"repos/{self.repo}/git/refs/heads/{ref}"],
                               capture_output=True, timeout=30)
        sha = self._api(f"repos/{self.repo}/git/ref/heads/{self.base}")["object"]["sha"]
        self._api(f"repos/{self.repo}/git/refs", "-X", "POST", "-f", f"ref=refs/heads/{branch}", "-f", f"sha={sha}")
        note = self.note(reason, sender)
        self._api(f"repos/{self.repo}/contents/wake/{stamp}.md", "-X", "PUT", "-f", f"message=WAKE: {reason[:60]}",
                  "-f", f"content={base64.b64encode(note.encode()).decode()}", "-f", f"branch={branch}")
        pr = self._api(f"repos/{self.repo}/pulls", "-X", "POST", "-f", f"title=WAKE: {reason[:90]}",
                       "-f", f"head={branch}", "-f", f"base={self.base}", "-f", f"body={note}")
        subprocess.run([self.gh, "api", f"repos/{self.repo}/issues/{pr['number']}/labels", "-X", "POST",
                        "-f", "labels[]=wake"], capture_output=True, timeout=30)
        return f"sent (PR {pr.get('html_url', '')})"


class RateLimitedWaker:
    def __init__(self, backend, state_dir: Path, clock: Clock, gap: int = 120):
        self.backend, self.clock, self.gap = backend, clock, gap
        state_dir = Path(state_dir)
        self.stamp_file = state_dir / "wake_last"
        self.queue_file = state_dir / "wake_queue.txt"
        self.lock_file = state_dir / "wake.lock"

    def _last(self) -> float:
        try:
            return float(self.stamp_file.read_text().strip() or 0)
        except (OSError, ValueError):
            return 0.0

    def pending(self) -> list[str]:
        try:
            return [x for x in self.queue_file.read_text("utf-8").splitlines() if x.strip()]
        except OSError:
            return []

    def _enqueue(self, reasons: list[str]) -> None:
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        with self.queue_file.open("a", encoding="utf-8") as fh:
            for r in reasons:
                fh.write(one_line(r, 300) + "\n")

    def _send(self, reasons: list[str], sender: str) -> str:
        text = reasons[0] if len(reasons) == 1 else f"{len(reasons)} updates: " + " || ".join(reasons)
        # Stamp the attempt either way: a broken backend is retried once per gap, not on every cycle.
        self.stamp_file.parent.mkdir(parents=True, exist_ok=True)
        self.stamp_file.write_text(str(self.clock.now()))
        try:
            return self.backend.send(text[:900], sender)
        except Exception as e:  # never raise into the caller: a wake-up is a nudge, the board already has the data
            self._enqueue(reasons)
            return f"failed ({one_line(str(e), 120)}), queued for retry"

    def wake(self, reason: str, sender: str = "PC", force: bool = False) -> str:
        reason = one_line(reason, 300)
        with file_lock(self.lock_file):
            since = self.clock.now() - self._last()
            if not force and since < self.gap:
                self._enqueue([reason])
                return f"queued (last wake-up {since:.0f} s ago, gap {self.gap} s)"
            return self._send([reason], sender)

    def flush(self, sender: str = "PC") -> str:
        """Called by the watcher every cycle: send everything queued as one wake-up once the gap has passed."""
        with file_lock(self.lock_file):
            queued = self.pending()
            if not queued or self.clock.now() - self._last() < self.gap:
                return ""
            self.queue_file.unlink(missing_ok=True)
            return f"{len(queued)} queued wake-up(s) sent as one: " + self._send(queued, sender)
