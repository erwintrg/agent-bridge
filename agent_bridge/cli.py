"""Command line: python -m agent_bridge <command>  (settings from .env, see .env.example)

  watch [--once] [--interval N]      the always-on watcher (run it as a systemd user service)
  turn run <id> | turn resume <id> <reply-id>    one session turn (started by the launcher, not by hand)
  status                             local sessions
  pulse [--print] [--force]          build and upload the PC pulse now
  context                            session-start context for interactive sessions (SessionStart hook)
  board read | add | set             read or edit the board by hand
  notify --title ... [--quiet] [--for MEMBER]    post a row for the cloud agent and wake it
  wake <reason> [--force]            wake the cloud agent (rate limited)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app import build, context_text
from .config import Config, load_env
from .rows import STATUSES
from .watcher import Watcher


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="agent_bridge", description="Task-board bridge between a cloud agent and a "
                                 "local coding agent.", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--env-file", help="settings file (default: $BRIDGE_ENV_FILE or ./.env)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch")
    w.add_argument("--once", action="store_true")
    w.add_argument("--interval", type=int)
    t = sub.add_parser("turn")
    t.add_argument("step", choices=["run", "resume"])
    t.add_argument("tid")
    t.add_argument("reply_id", nargs="?")
    sub.add_parser("status")
    p = sub.add_parser("pulse")
    p.add_argument("--print", action="store_true")
    p.add_argument("--force", action="store_true")
    sub.add_parser("context")
    bd = sub.add_parser("board").add_subparsers(dest="bcmd", required=True)
    bd.add_parser("read")
    ba = bd.add_parser("add")
    ba.add_argument("--owner", required=True)
    ba.add_argument("--title", required=True)
    ba.add_argument("--detail", default="")
    ba.add_argument("--by", default="")
    bs = bd.add_parser("set")
    bs.add_argument("id", help="row id, '-' for a blank id (needs --row)")
    bs.add_argument("--row", type=int, help="sheet row number; the row must still hold the id")
    bs.add_argument("--status", choices=STATUSES)
    bs.add_argument("--result")
    bs.add_argument("--new-id", action="store_true", help="give the row the next free id")
    n = sub.add_parser("notify")
    n.add_argument("--title", required=True)
    n.add_argument("--detail", default="")
    n.add_argument("--quiet", action="store_true", help="add the row without a wake-up")
    n.add_argument("--for", dest="member", default="", help="team member the row is meant for")
    wk = sub.add_parser("wake")
    wk.add_argument("reason", nargs="+")
    wk.add_argument("--force", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    env = load_env(a.env_file)
    cfg = Config.from_env(env, base=Path(env["_ENV_FILE"]).parent)
    b = build(cfg)
    if a.cmd == "watch":
        return Watcher(b).run(a.interval or cfg.interval, once=a.once)
    if a.cmd == "turn":
        if a.step == "resume" and not a.reply_id:
            print("turn resume needs <id> <reply-id>", file=sys.stderr)
            return 2
        b.sessions.dispatch([a.step, a.tid] + ([a.reply_id] if a.reply_id else []))
        return 0
    if a.cmd == "status":
        print(b.sessions.status_text())
        return 0
    if a.cmd == "pulse":
        if a.print:
            print(b.pulse.build())
        print(b.pulse.run(force=a.force) or "pulse unchanged")
        return 0
    if a.cmd == "context":
        print(context_text(b))
        return 0
    if a.cmd == "board":
        if a.bcmd == "read":
            for r in b.board.rows():
                print(json.dumps({"row": r.rownum, **r.values()}, ensure_ascii=False))
        elif a.bcmd == "add":
            print(b.board.add(owner=a.owner, title=a.title, detail=a.detail, requested_by=a.by))
        else:
            print(b.board.set(a.id, row=a.row, status=a.status, result=a.result, new_id=a.new_id))
        return 0
    if a.cmd == "notify":
        # Other PC-side watchers (a mail watcher, a build watcher) report to the cloud agent the same way.
        # Low-value rows go in quietly; the agent sees them on its next wake-up.
        tid = b.board.add(owner=cfg.agent_name, title=a.title, detail=a.detail, requested_by=cfg.local_owner)
        print(tid)
        if not a.quiet:
            reason = f"new board row {tid} for you: {a.title[:120]}"
            print("wake-up:", b.waker.wake(f"[for {a.member}] {reason}" if a.member else reason, sender=cfg.local_owner))
        return 0
    if a.cmd == "wake":
        print(b.waker.wake(" ".join(a.reason), sender=cfg.local_owner, force=a.force))
        return 0
    return 2
