#!/usr/bin/env python3
"""Offline demo of agent-bridge.

A simulated cloud agent writes board rows, the real watcher handles them, and a scripted mock stands in for the
local coding agent. Local backends only: a JSON board, a folder as the shared drive, a log file as the wake-up
channel. Simulated time, 45 s per watcher cycle. No account, key or network needed.

    python demo.py            (everything it writes lands in ./demo-run/, wiped at the start)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent_bridge.app import build  # noqa: E402
from agent_bridge.clock import SimClock  # noqa: E402
from agent_bridge.config import Config  # noqa: E402
from agent_bridge.util import one_line  # noqa: E402
from agent_bridge.watcher import Watcher  # noqa: E402

RUN = ROOT / "demo-run"
FIX = ROOT / "fixtures"
START = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp()
CYCLE = 45
INVOICES = "Find the unpaid invoices and draft payment reminders"


def clip(s: str, n: int) -> str:
    return one_line(s, n)


def say(s: str = "") -> None:
    print(s)


# ---------------------------------------------------------------------------------------------------- setup
def touch(path: Path, ts: float, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    os.utime(path, (ts, ts))


def prepare() -> Config:
    shutil.rmtree(RUN, ignore_errors=True)
    # what the human asked the local agent at the desk this morning (a Claude Code transcript, made up)
    fake_key = "sk-" + "demo" + "X7f2" * 5      # built at runtime: no secret-shaped string sits in the repo
    iso = lambda minutes: datetime.fromtimestamp(START - minutes * 60, timezone.utc).isoformat().replace("+00:00", "Z")
    lines = [
        {"type": "summary", "summary": "Project update for the team"},
        {"type": "user", "timestamp": iso(50), "message": {"role": "user", "content":
            "Draft a short project update for the team about the onboarding flow"}},
        {"type": "assistant", "timestamp": iso(49), "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Here is a 5-line draft. It covers the Monday launch and the staging move."}]}},
        {"type": "user", "timestamp": iso(20), "message": {"role": "user", "content":
            f"Use this staging value in the config, it is {fake_key}"}},
        {"type": "assistant", "timestamp": iso(19), "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Done, the staging config is updated. I did not echo the value anywhere."}]}},
        {"type": "user", "timestamp": iso(18), "isMeta": True, "message": {"role": "user", "content": "<local-command>"}},
    ]
    touch(RUN / "transcripts" / "session-1.jsonl", START - 18 * 60, "\n".join(json.dumps(x) for x in lines) + "\n")
    # the local agent's memory folder (two older notes)
    for f in sorted((FIX / "demo" / "memory").glob("*.md")):
        touch(RUN / "memory" / f.name, START - 2 * 86400, f.read_text("utf-8"))
    # a work folder: recent files are listed by name, old ones and secret-looking ones never
    ws = RUN / "workspace"
    touch(ws / "notes" / "meeting-2026-10-01.md", START - 2 * 3600, "made-up meeting notes\n")
    touch(ws / "notes" / "plan.md", START - 26 * 3600, "made-up plan\n")
    touch(ws / "invoices" / "ledger.csv", START - 3 * 3600, "client,amount,paid\nAcme Example,400,no\n")
    touch(ws / "archive" / "old-report.md", START - 10 * 86400, "old\n")
    touch(ws / ".env", START - 3600, "EXAMPLE_SETTING=1\n")
    touch(ws / "config" / "api-token.txt", START - 3600, "placeholder\n")
    return Config(
        state_dir=RUN / "state", board="json", board_path=RUN / "board.json",
        store="local", shared_dir=RUN / "shared", wake="log", wake_log=RUN / "wake.log", wake_gap=120,
        runner="mock", mock_script=FIX / "mock_agent.json", launcher="inline", max_sessions=2,
        local_owner="PC", agent_name="AGENT", tz="UTC", jobs_file=FIX / "jobs.json", pulse_every=90,
        transcripts_dir=RUN / "transcripts", memory_dir=RUN / "memory", watch_dirs=[ws], system_checks=False,
    )


# ---------------------------------------------------------------------------------------------------- display
def show_board(b, before: list) -> list:
    rows = b.board.rows()
    snap = [r.values() for r in rows]
    if snap == before:
        say("board: unchanged")
        return snap
    say("board:")
    say(f"  {'row':>3}  {'id':7} {'status':12} {'title':38} result")
    for r in rows:
        res = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} ", "", r.result.split("\n---")[0].split("\n>>")[0])
        say(f"  {r.rownum:>3}  {r.id or '-':7} {r.status:12} {clip(r.title, 38):38} {clip(res, 46)}".rstrip())
    return snap


def show_sessions(b) -> None:
    st = b.sessions.all()
    if st:
        say("local sessions:")
        for tid, s in st.items():
            say(f"  {tid}  {s.get('status'):12} {s.get('turns', 0)} turn(s)  session {s.get('session')}")
    sent = len((RUN / "wake.log").read_text("utf-8").splitlines()) if (RUN / "wake.log").exists() else 0
    say(f"wake-ups: {sent} sent so far, {len(b.waker.pending())} queued")


class CloudAgent:
    """Stands in for the hosted chief-of-staff agent. It can only write board rows and files in the shared
    folder, exactly like the real one."""

    def __init__(self, b):
        self.b = b

    def post(self, title: str, tid=None, note: str = "") -> None:
        got = self.b.board.add(owner="PC", title=title, requested_by="AGENT", tid=tid)
        say(f"  {got or '(no id)':9} {title}{'   <- ' + note if note else ''}")

    def write_status(self) -> None:
        self.b.store.write("agent-status.md", (FIX / "demo" / "agent-status.md").read_text("utf-8"))
        say("  rewrites agent-status.md in the shared folder (a deadline the user told it on the go)")


def spotlight(b, tid: str) -> None:
    r = next(r for r in b.board.rows() if r.id == tid)
    say(f"row {tid} result, as the cloud agent reads it:")
    for para in r.result.split("\n"):
        say(textwrap.fill(para, 100, initial_indent="  | ", subsequent_indent="  | "))


def cycle(n: int, b, clock, watcher, snap: list, intro: str = "", actions=None, focus: str = "") -> list:
    t = clock.dt()
    say(f"\n=== cycle {n}  {t.strftime('%H:%M:%S')}  {'=' * 76}"[:100])
    if intro:
        say(intro)
    if actions:
        actions()
    say("watcher:")
    handled = watcher.tick()
    if handled == 0:
        say("  (nothing to do for the rows)")
    snap = show_board(b, snap)
    show_sessions(b)
    if focus:
        spotlight(b, focus)
    clock.advance(CYCLE)
    return snap


def print_wake_log() -> None:
    say("\n=== wake log: what the cloud agent received " + "=" * 54)
    for line in (RUN / "wake.log").read_text("utf-8").splitlines():
        stamp, _, rest = line.partition("  WAKE AGENT <- PC: ")
        m = re.match(r"^(\d+) updates: (.*)$", rest)
        if m:
            say(f"{stamp[11:]}  {m.group(1)} updates in one wake-up:")
            for part in m.group(2).split(" || "):
                say(f"       - {part}")
        else:
            say(f"{stamp[11:]}  {rest}")


def print_pulse() -> None:
    body = (RUN / "shared" / "pc-status.md").read_text("utf-8")
    say("\n=== pc-status.md in the shared folder (excerpt): what the cloud agent knows about the PC " + "=" * 9)
    keep = ("## Health", "## Human <-> local agent", "## Memory written", "## Files changed")
    for block in body.split("\n## ")[1:]:
        if any(("## " + block).startswith(k) for k in keep):
            say("## " + block.strip())


# ---------------------------------------------------------------------------------------------------- story
def main() -> int:
    cfg = prepare()
    clock = SimClock(START, tz="UTC")
    b = build(cfg, clock=clock, log=lambda m: say("  " + m))
    agent, watcher, snap = CloudAgent(b), Watcher(b), []
    say("agent-bridge demo: a simulated cloud agent, the real watcher, a scripted mock local agent.")
    say(f"Everything runs offline on simulated time ({CYCLE} s per cycle). Files land in {RUN.name}/.")

    def c1():
        say("cloud agent writes 4 rows (the user asked it for things from the phone):")
        agent.post("GET calendar 3")
        agent.post(INVOICES)
        agent.post(INVOICES, tid="", note="the same request written again, without an id")
        agent.post("Summarize today's meeting notes in 3 bullets", tid="", note="a new request without an id")
    snap = cycle(1, b, clock, watcher, snap, actions=c1, focus="T-0002")

    def c2():
        say("cloud agent (woken at 09:00) answers the question in T-0002:")
        agent.post("REPLY T-0002 skip Acme Example, they paid by phone")
        agent.post("REPLY T-0002 skip Acme Example, they paid by phone", tid="T-0004",
                   note="the same reply retried under the same id")
        agent.post("GET passwords", note="not on the allow-list")
    snap = cycle(2, b, clock, watcher, snap, actions=c2, focus="T-0002")

    def c3():
        say("meanwhile at the desk: the user tells the local agent something durable, it lands in memory")
        touch(RUN / "memory" / "feedback_reminders_as_drafts.md", clock.now(),
              "---\nname: Reminders as drafts\ndescription: Client reminders always start as drafts; the user "
              "sends them personally.\ntype: feedback\n---\nReminders are drafts first.\n")
    snap = cycle(3, b, clock, watcher, snap, actions=c3,
                 intro="(no new rows: the cloud agent's wake-ups are still inside the 2 min gap)")
    snap = cycle(4, b, clock, watcher, snap, intro="(the gap has passed: the queue goes out as ONE wake-up)")

    def c5():
        say("cloud agent (woken at 09:02) asked the user about T-0002 and relays the answer:")
        agent.post("REPLY T-0002 no, keep both as drafts, the user sends them personally")
        agent.write_status()
    snap = cycle(5, b, clock, watcher, snap, actions=c5)
    snap = cycle(6, b, clock, watcher, snap, intro="(quiet cycle)")
    snap = cycle(7, b, clock, watcher, snap, intro="(quiet cycle, the gap has passed again)")

    print_wake_log()
    print_pulse()
    say(f"\nInspect: {RUN.name}/board.json, {RUN.name}/wake.log, {RUN.name}/shared/ (pulse + results), "
        f"{RUN.name}/state/sessions/ (prompts and turn logs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
