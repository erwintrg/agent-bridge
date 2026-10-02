"""Keep both agents on the same page, in both directions.

PC -> agent: `pc-status.md` in the shared folder, rebuilt every few minutes and uploaded only when its content
changed: health, what the human asked the local agent lately (secrets redacted), the sessions the cloud agent
started, memory files written lately, files that changed (names only, never contents), open board rows.

Agent -> PC: the cloud agent keeps ONE file `agent-status.md` in the same folder (what the human told it on the go,
commitments, deadlines, corrections). The PC pulls it when it changes; every session prompt includes it.

Fact sync ("tell one agent, both know"):
- agent-status.md changed -> a board row "SYNC agent-status" starts a local session that checks the new facts
  against the PC's memory, saves what is new and lists corrections for the agent (at most once per sync gap).
- PC memory changed -> the cloud agent is woken with the names of the changed memory files (at most once per gap).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from .redact import SKIP_NAME, redact
from .util import one_line, read_json, write_json

PULSE_FILE = "pc-status.md"
AGENT_FILE = "agent-status.md"
PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cache", "dist", "build", ".next", ".pytest_cache"}
HOT = ("open", "in-progress", "needs-agent", "needs-human", "blocked")

SYNC_DETAIL = """The cloud agent rewrote {file} (pulled to {path}; the previous version is agent-status.prev.md
next to it, diff them). Reconcile its side with this computer so the user never has to tell two agents the same thing:
1. Every fact, decision, deadline, commitment or correction that is NEW or CHANGED: check it against your memory and
   the files it concerns.
2. New durable facts this computer lacks: save them, marked "(per the cloud agent, <date>)".
3. Conflicts: the newer statement that came from the user wins; fix the stale side. If this computer is newer, list
   the correction for the agent. If you cannot tell which is right and it matters (money, deadlines, health, legal,
   clients), finish with ASK {AGENT}.
4. Deadlines in the next 14 days on either side: both sides must have them with the same date and time.
Finish with: "Saved on PC: ...", "Corrections for the agent: ...", "Deadlines checked: ...". Nothing outward.
If nothing is new: one line."""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") == "text")
    return ""


def _iso(ts) -> float | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class Pulse:
    def __init__(self, *, cfg, clock, board, store, waker, sessions, log=print):
        self.cfg, self.clock, self.board, self.store = cfg, clock, board, store
        self.waker, self.sessions, self.log = waker, sessions, log
        self.state_file = cfg.state_dir / "pulse_state.json"
        self.from_agent = cfg.state_dir / "from-agent"

    @property
    def agent_status_path(self) -> Path:
        return self.from_agent / AGENT_FILE

    # sections ---------------------------------------------------------------------------------------------------
    def sec_health(self) -> list[str]:
        out = []
        ws = read_json(self.cfg.state_dir / "watch_state.json", {})
        last = ws.get("last_cycle")
        if last is None:
            out.append("- Watcher: no cycle recorded yet")
        else:
            age = (self.clock.now() - float(last)) / 60
            if ws.get("blind"):
                out.append(f"- Watcher: BLIND, the board was unreadable at the last cycle ({age:.0f} min ago)")
            else:
                out.append("- Watcher: running" if age < 10 else f"- Watcher: SILENT for {age:.0f} min")
        busy = sum(1 for s in self.sessions.all().values() if s.get("status") == "running")
        out.append(f"- Local sessions running: {busy} of {self.cfg.max_sessions}")
        if self.cfg.system_checks:
            if shutil.which("systemctl"):
                r = subprocess.run(["systemctl", "--user", "--failed", "--plain", "--no-legend"],
                                   capture_output=True, text=True)
                units = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
                out.append(f"- Failed background jobs: {', '.join(units) if units else 'none'}")
            if shutil.which("uptime"):
                up = subprocess.run(["uptime", "-p"], capture_output=True, text=True).stdout.strip()
                if up:
                    out.append(f"- PC: on ({up})")
        return out

    def sec_prompts(self, hours: int = 48) -> list[str]:
        d = self.cfg.transcripts_dir
        if not d or not Path(d).is_dir():
            return ["- (no transcript folder configured)"]
        cutoff = self.clock.now() - hours * 3600
        found = []
        for f in sorted(Path(d).glob("*.jsonl")):
            try:
                if f.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            title, prompts, answer = "", [], ""
            for line in f.open(encoding="utf-8", errors="replace"):
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                kind = j.get("type")
                if kind == "ai-title":
                    title = j.get("aiTitle") or title
                elif kind == "summary":
                    title = j.get("summary") or title
                elif kind in ("user", "assistant"):
                    ts = _iso(j.get("timestamp"))
                    txt = _text((j.get("message") or {}).get("content")).strip()
                    if ts is None or ts < cutoff or not txt:
                        continue
                    if kind == "assistant":
                        answer = txt
                    elif not (j.get("isMeta") or txt.startswith(("<", "[SYSTEM", "Caveat:", "This session is being continued"))):
                        prompts.append((ts, txt))
            if prompts:
                found.append((prompts[-1][0], title, prompts, answer))
        found.sort(key=lambda x: x[0], reverse=True)
        out = []
        for last, title, prompts, answer in found[:12]:
            # redact BEFORE shortening: a key cut in half could slip under the pattern's minimum length
            out.append(f"- **{one_line(redact(title), 80) or 'Session'}** "
                       f"(last message {self.clock.dt(last).strftime('%a %d.%m %H:%M')})")
            for ts, p in prompts[-6:]:
                out.append(f"  - {self.clock.dt(ts).strftime('%H:%M')} human: {one_line(redact(p), 220)}")
            if answer:
                out.append(f"  - local agent's latest answer: {one_line(redact(answer), 300)}")
        return out or [f"- no local sessions in the last {hours} h"]

    def sec_sessions(self) -> list[str]:
        st = self.sessions.all()
        rows = sorted(st.items(), key=lambda kv: kv[1].get("created", ""), reverse=True)[:8]
        return [f"- {tid}: {s.get('status')} (turns {s.get('turns', 0)}) {one_line(s.get('title', ''), 90)}"
                for tid, s in rows] or ["- none yet"]

    def sec_memory(self, days: int = 7) -> list[str]:
        d = self.cfg.memory_dir
        if not d or not Path(d).is_dir():
            return ["- (no memory folder configured)"]
        cutoff = self.clock.now() - days * 86400
        files = sorted((f for f in Path(d).glob("*.md") if f.name != "MEMORY.md" and f.stat().st_mtime > cutoff),
                       key=lambda f: f.stat().st_mtime, reverse=True)
        out = []
        for f in files[:15]:
            m = re.search(r"^description:\s*(.+)$", f.read_text("utf-8", errors="replace"), re.M)
            when = self.clock.dt(f.stat().st_mtime).strftime("%d.%m %H:%M")
            out.append(f"- {when} `{f.stem}`: {one_line(redact(m.group(1) if m else ''), 220)}")
        return out or [f"- nothing written in the last {days} days"]

    def sec_files(self, hours: int = 48) -> list[str]:
        cutoff = self.clock.now() - hours * 3600
        state = self.cfg.state_dir.resolve()
        groups: dict[str, list[tuple[float, str]]] = {}
        for base in self.cfg.watch_dirs:
            base = Path(base)
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(x for x in dirnames if x not in PRUNE and not x.startswith(".")
                                     and Path(dirpath, x).resolve() != state)
                rel = Path(dirpath).relative_to(base).parts
                for name in filenames:
                    if name.startswith(".") or SKIP_NAME.search(name):
                        continue        # secret-looking files are not even listed by name
                    try:
                        mt = Path(dirpath, name).stat().st_mtime
                    except OSError:
                        continue
                    if mt >= cutoff:
                        key = f"{base.name}/" + "/".join(rel[:3])
                        groups.setdefault(key, []).append((mt, name))
        out = []
        for key, items in sorted(groups.items(), key=lambda kv: max(m for m, _ in kv[1]), reverse=True)[:20]:
            items.sort(reverse=True)
            more = f" (+{len(items) - 4} more)" if len(items) > 4 else ""
            out.append(f"- {key} [{len(items)}]: {', '.join(n for _, n in items[:4])}{more}")
        return out or ["- nothing changed"]

    def sec_board(self) -> list[str]:
        try:
            rows = self.board.rows()
        except Exception as e:
            return [f"- (board unreadable: {one_line(str(e), 100)})"]
        out = [f"- {r.id or '(no id)'} [{r.status}] {r.owner}: {one_line(r.title, 110)}" for r in rows
               if r.status in HOT and (r.owner == self.cfg.local_owner or r.status == "needs-human")]
        return out[-15:] or ["- nothing open for the PC or the user"]

    def build(self) -> str:
        now = self.clock.dt()
        owner = self.cfg.local_owner
        parts = [
            "# PC pulse (the local agent's side of the picture)", "",
            f"_Updated {now.strftime('%a %d.%m.%Y %H:%M')} {now.tzname()}. Rebuilt within a few minutes of any change "
            f"while the PC is on. If this timestamp is old, the PC is off or asleep: say so, do not guess._", "",
            f"How to use: ground truth for what happened on the PC. Ask the PC to DO something: a board row "
            f"owner={owner} in plain words (GET <job> for instant data). Answer a PC question: a row "
            f"\"REPLY T-xxxx <answer>\".", "",
            "## Health", *self.sec_health(), "",
            "## Human <-> local agent (last 48 h, secrets redacted)", *self.sec_prompts(), "",
            "## Requests from the cloud agent (local sessions)", *self.sec_sessions(), "",
            "## Memory written in the last 7 days", *self.sec_memory(), "",
            "## Files changed in the last 48 h (names only)", *self.sec_files(), "",
            "## Open on the board for the PC or the user", *self.sec_board(), "",
        ]
        return "\n".join(parts) + "\n"

    # sync -------------------------------------------------------------------------------------------------------
    def pull_agent_status(self, st: dict) -> str:
        marker = self.store.modified(AGENT_FILE)
        if marker is None or (marker == st.get("agent_marker") and self.agent_status_path.exists()):
            return ""
        got = self.store.read(AGENT_FILE)
        if got is None:
            return ""
        text, marker = got
        self.from_agent.mkdir(parents=True, exist_ok=True)
        if self.agent_status_path.exists():
            self.agent_status_path.replace(self.from_agent / "agent-status.prev.md")
        self.agent_status_path.write_text(text, "utf-8")
        st["agent_marker"], st["agent_changed_pending"] = marker, True
        return f"pulled the changed {AGENT_FILE}"

    def maybe_sync(self, st: dict) -> str:
        if not st.get("agent_changed_pending"):
            return ""
        if self.clock.now() - float(st.get("sync_row_at", 0)) < self.cfg.sync_gap:
            return "SYNC due (waiting for the sync gap)"
        detail = SYNC_DETAIL.format(AGENT=self.cfg.agent_name.upper(), file=AGENT_FILE,
                                    path=self.agent_status_path)
        tid = self.board.add(owner=self.cfg.local_owner, requested_by="bridge-sync",
                             title=f"SYNC agent-status ({self.clock.stamp()})", detail=detail)
        st["agent_changed_pending"], st["sync_row_at"] = False, self.clock.now()
        return f"SYNC row {tid} added"

    def maybe_memory_wake(self, st: dict) -> str:
        d = self.cfg.memory_dir
        if not d or not Path(d).is_dir():
            return ""
        mem = sorted(Path(d).glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        newest = mem[0].stat().st_mtime if mem else 0.0
        if newest <= float(st.get("mem_seen", 0)):
            return ""
        if not st.get("mem_seen"):
            st["mem_seen"] = newest           # first run: baseline only
            return ""
        if self.clock.now() - float(st.get("mem_wake_at", 0)) < self.cfg.sync_gap:
            return ""
        changed = [f.stem for f in mem if f.stat().st_mtime > float(st["mem_seen"]) and f.name != "MEMORY.md"][:8]
        names = ", ".join(changed or ["MEMORY.md"])
        w = self.waker.wake(f"PC memory changed: {names}. Read {PULSE_FILE} (Memory section) and update your side; "
                            f"conflicts: a row owner={self.cfg.local_owner}.", sender=self.cfg.local_owner)
        st["mem_seen"], st["mem_wake_at"] = newest, self.clock.now()
        return f"memory changed ({names}), wake-up {w}"

    def run(self, force: bool = False) -> str:
        st = read_json(self.state_file, {})
        msgs = []
        body = self.build()
        digest = hashlib.sha256(re.sub(r"^_Updated .*$", "", body, flags=re.M).encode()).hexdigest()
        try:
            if force or digest != st.get("pulse_hash"):
                self.store.write(PULSE_FILE, body)
                st["pulse_hash"] = digest
                msgs.append(f"{PULSE_FILE} uploaded")
            msgs.append(self.pull_agent_status(st))
        except Exception as e:
            msgs.append(f"shared folder error: {one_line(str(e), 120)}")
        (self.cfg.state_dir / PULSE_FILE).write_text(body, "utf-8")   # local copy for `GET pulse`
        for step in (self.maybe_sync, self.maybe_memory_wake):
            try:
                msgs.append(step(st))
            except Exception as e:
                msgs.append(f"{step.__name__} failed: {one_line(str(e), 120)}")
        write_json(self.state_file, st)
        return "; ".join(m for m in msgs if m)
