"""Agent runners: one call = one session turn.

`ClaudeCliRunner` runs Claude Code headless (`claude -p ... --output-format json`) and continues a session with
`--resume <session_id>`. `MockRunner` plays a scripted agent from a JSON file, for the offline demo and the tests."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .util import file_lock, read_json, write_json


@dataclass
class TurnResult:
    text: str
    session_id: str | None
    ok: bool


def parse_claude_json(stdout: str, fallback_session: str | None) -> TurnResult:
    """The last stdout line of `claude -p --output-format json` is one JSON object with result, session_id and
    is_error. Anything else (a crash, a login prompt) becomes a failed turn carrying the tail of the output."""
    lines = (stdout or "").strip().splitlines()
    try:
        j = json.loads(lines[-1])
        return TurnResult((j.get("result") or "").strip(), j.get("session_id") or fallback_session,
                          not j.get("is_error"))
    except (IndexError, ValueError, AttributeError):
        return TurnResult((stdout or "").strip()[-1500:] or "(no output)", fallback_session, False)


class ClaudeCliRunner:
    def __init__(self, bin: str = "claude", model: str = "", permission_mode: str = "acceptEdits",
                 allowed_tools: str = "", workdir: Path = Path("."), timeout: int = 45 * 60):
        self.bin, self.model, self.permission_mode = bin, model, permission_mode
        self.allowed_tools, self.workdir, self.timeout = allowed_tools, Path(workdir), timeout

    def argv(self, prompt: str, session_id: str | None) -> list[str]:
        argv = [self.bin, "-p", prompt, "--output-format", "json"]
        if self.model:
            argv += ["--model", self.model]
        if self.permission_mode:
            argv += ["--permission-mode", self.permission_mode]
        if self.allowed_tools:
            argv += ["--allowedTools", self.allowed_tools]
        if session_id:
            argv += ["--resume", session_id]
        return argv

    def run(self, prompt: str, session_id: str | None, *, log_path: Path, meta: dict) -> TurnResult:
        try:
            r = subprocess.run(self.argv(prompt, session_id), cwd=self.workdir, capture_output=True, text=True,
                               timeout=self.timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return TurnResult(f"(turn timed out after {self.timeout // 60} min)", session_id, False)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(r.stdout + "\n" + r.stderr + "\n")
        return parse_claude_json(r.stdout, session_id)


class MockRunner:
    """Scripted stand-in for Claude. The script maps a phrase in the request title to the texts of turns 1, 2, 3...
    `{answer}` in a turn is replaced by the reply that resumed it. Session ids are mock-0001, mock-0002, ... and
    live in a state file, so a resume works across processes exactly like a real session."""

    def __init__(self, script_path: Path, state_path: Path):
        self.script = read_json(script_path, {"scripts": []})
        self.state_path = Path(state_path)

    def _match(self, title: str) -> int | None:
        low = (title or "").lower()
        for i, s in enumerate(self.script.get("scripts", [])):
            if s.get("match", "").lower() in low:
                return i
        return None

    def run(self, prompt: str, session_id: str | None, *, log_path: Path, meta: dict) -> TurnResult:
        with file_lock(self.state_path.with_name(self.state_path.name + ".lock")):
            return self._run(prompt, session_id, log_path=log_path, meta=meta)

    def _run(self, prompt: str, session_id: str | None, *, log_path: Path, meta: dict) -> TurnResult:
        st = read_json(self.state_path, {"counter": 0, "sessions": {}})
        if session_id is None:
            st["counter"] += 1
            session_id = f"mock-{st['counter']:04d}"
            st["sessions"][session_id] = {"script": self._match(meta.get("title", "")), "turn": 0}
        elif session_id in st["sessions"]:
            st["sessions"][session_id]["turn"] += 1
        else:
            return TurnResult(f"mock agent: unknown session {session_id}", session_id, False)
        s = st["sessions"][session_id]
        turns = self.script["scripts"][s["script"]]["turns"] if s["script"] is not None else []
        if s["turn"] < len(turns):
            text = turns[s["turn"]]
        else:
            text = self.script.get("fallback", "Done. (mock agent: no scripted turn left)")
        text = text.replace("{answer}", (meta.get("answer") or "").strip())
        write_json(self.state_path, st)
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"--- prompt (session {session_id}, turn {s['turn'] + 1})\n{prompt}\n--- answer\n{text}\n")
        return TurnResult(text, session_id, True)
