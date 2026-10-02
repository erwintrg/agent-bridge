"""The always-on watcher. Every cycle (default 45 s):

  1. housekeeping: flush queued wake-ups, run the pulse when it is due
  2. ingest task drops (task-*.json in the shared folder, for agents that cannot write the board directly)
  3. read the board; one pass in board order, for rows owner=<local owner> and status open:
       - duplicate guard: a repeated request title (within 1 h) or a reused id is closed BY ROW NUMBER
       - a row without an id gets the next free id and runs next cycle
       - "REPLY T-xxxx ..."  -> resumes that row's session
       - "GET <job> [param]" -> allow-listed read-only job, answered at once
       - anything else       -> its own local agent session (max N at once; the rest wait their turn)

The PC is never exposed to the internet: it polls the board and pushes files, nothing listens for connections."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .rows import Row, parse_job_request, parse_reply, title_key
from .util import one_line, write_json

DUP_WINDOW = 3600          # a repeated title within this window is a duplicate write, not a new request
HEARTBEAT_EVERY = 300
SUMMARY_CELL = 900


def _pid_alive(pid: int) -> bool:
    if os.name != "posix":                      # pragma: no cover
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    # A live pid is not proof: after a reboot the pid in a stale lock file is often reused by another process,
    # and a plain "is it alive" check then keeps the watcher from ever starting.
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        return b"agent_bridge" in cmd or b"agent-bridge" in cmd
    except OSError:
        return True                             # no /proc (macOS): trust the signal check


def acquire_lock(lock: Path) -> bool:
    """Single instance: two watchers would race on the same rows."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        other = int(lock.read_text().strip() or 0)
    except (OSError, ValueError):
        other = 0
    if other and other != os.getpid() and _pid_alive(other):
        return False
    lock.write_text(str(os.getpid()))
    return True


class Watcher:
    def __init__(self, bridge):
        self.b = bridge
        self.cfg = bridge.cfg
        self._last_pulse: float | None = None
        self._last_beat = bridge.clock.now()

    def log(self, msg: str) -> None:
        self.b.log(msg)

    # housekeeping -----------------------------------------------------------------------------------------------
    def housekeeping(self) -> None:
        try:
            m = self.b.waker.flush(self.cfg.local_owner)
            if m:
                self.log(f"wake-up queue: {m}")
        except Exception as e:
            self.log(f"wake-up flush failed: {e}")
        now = self.b.clock.now()
        if self._last_pulse is None or now - self._last_pulse >= self.cfg.pulse_every:
            self._last_pulse = now
            try:
                m = self.b.pulse.run()
                if m:
                    self.log(f"pulse: {m}")
            except Exception as e:
                self.log(f"pulse failed: {e}")

    def ingest(self) -> int:
        """Never turn an unreadable drop into a silent blank row: leave it in place for a person."""
        n = 0
        for name in self.b.store.list("task-"):
            if not name.endswith(".json"):
                continue
            got = self.b.store.read(name)
            if got is None:
                continue
            try:
                d = json.loads(got[0])
            except ValueError:
                self.log(f"drop {name}: not JSON, left in place")
                continue
            if not isinstance(d, dict):
                self.log(f"drop {name}: not an object, left in place")
                continue
            title, detail = str(d.get("title") or "").strip(), str(d.get("detail") or "").strip()
            if not (title or detail):
                self.log(f"drop {name}: no title or detail, left in place")
                continue
            tid = self.b.board.add(owner=str(d.get("owner") or self.cfg.local_owner), title=title or one_line(detail, 80),
                                   detail=detail, requested_by=str(d.get("requested_by") or self.cfg.agent_name))
            self.b.store.delete(name)
            self.log(f"drop {name} -> row {tid}")
            n += 1
        return n

    # the cycle --------------------------------------------------------------------------------------------------
    def _save_state(self, blind: bool) -> None:
        write_json(self.cfg.state_dir / "watch_state.json", {"last_cycle": self.b.clock.now(), "blind": blind})

    def cycle(self) -> int:
        """Rows handled, or -1 when the board could not be read (blind is not the same as idle)."""
        try:
            self.ingest()
        except Exception as e:
            self.log(f"ingest failed: {e}")
        try:
            rows = self.b.board.rows()
        except Exception as e:
            self.log(f"board read failed: {e}")
            self._save_state(blind=True)
            return -1
        self._save_state(blind=False)
        write_json(self.cfg.state_dir / "board_cache.json", [r.values() for r in rows])
        handled = 0
        seen_titles: dict[str, str] = {}
        seen_ids: set[str] = set()
        cutoff = self.b.clock.now() - DUP_WINDOW
        me = self.cfg.local_owner
        for r in rows:
            try:
                handled += self._handle(r, me, cutoff, seen_titles, seen_ids)
            except Exception as e:      # one bad row must not stop the rest of the board
                self.log(f"row {r.rownum} ({r.id or 'no id'}) failed: {type(e).__name__}: {e}")
        return handled

    def _handle(self, r: Row, me: str, cutoff: float, seen_titles: dict, seen_ids: set) -> int:
        tid = r.id.strip()
        created = self.b.clock.parse_stamp(r.created)
        recent = created is None or created >= cutoff
        key = title_key(r.title)
        is_job = parse_job_request(r.title) is not None
        mine_open = r.owner == me and r.status == "open"
        dup_of = None
        if mine_open and not is_job and recent and key in seen_titles:
            dup_of = seen_titles[key]
        elif mine_open and tid and tid in seen_ids:
            dup_of = f"{tid} (same id written twice)"
        if dup_of:
            self.b.board.set(tid or "-", row=r.rownum, status="done", result=f"duplicate of {dup_of}; not run again")
            self.log(f"row {r.rownum} ({tid or 'no id'}): duplicate of {dup_of}, closed by row number")
            return 1
        if tid:
            seen_ids.add(tid)
        if r.owner == me and not is_job and recent and r.status != "blocked":
            seen_titles.setdefault(key, tid or f"row {r.rownum}")   # a retry after "blocked" is not a duplicate
        if not mine_open:
            return 0
        if not tid:
            nid = self.b.board.set("-", row=r.rownum, new_id=True)
            self.log(f"row {r.rownum} had no id: now {nid}, runs next cycle")
            return 1
        return self._route(r)

    def _route(self, r: Row) -> int:
        b, tid = self.b, r.id.strip()
        reply = parse_reply(r.title)
        if reply:
            target, text = reply
            if b.sessions.resumable(target) and not b.sessions.has_slot():
                return 0                # the resumed turn needs a free slot too; the reply waits for one
            answer = "\n\n".join(x for x in (text, r.detail) if x.strip())
            state, msg = b.sessions.resume(target, answer, tid)
            if state == "held":
                return 0                # the session is mid-turn: the reply row stays open, retried next cycle
            b.board.set(tid, row=r.rownum, status="done" if state == "resumed" else "blocked", result=msg)
            if state != "resumed":
                self.log(msg)
            return 1
        job = parse_job_request(r.title)
        if job is None:
            if not b.sessions.has_slot():
                return 0                # all session slots busy: the row stays open until one frees up
            try:
                b.sessions.start(tid, r.title, r.detail, r.requested_by, rownum=r.rownum)
            except Exception as e:
                b.board.set(tid, row=r.rownum, status="blocked", result=f"could not start a local session: {e}")
                self.log(f"{tid}: session start failed: {e}")
            return 1
        name, param = job
        err = b.jobs.check(name, param)
        if err:
            b.board.set(tid, row=r.rownum, status="blocked", result=f"{err}. {b.jobs.help()}")
            self.log(f"{tid}: {r.title.strip()} -> blocked, {err}")
            return 1
        t0 = time.monotonic()
        ok, out = b.jobs.run(name, param)
        fname = f"result-{tid}.md"
        header = (f"# {r.title.strip()}\n\n_ran {b.clock.stamp()}, {time.monotonic() - t0:.0f} s, "
                  f"{'ok' if ok else 'FAILED'}_\n\n```\n")
        try:
            b.store.write(fname, header + out[:400_000] + "\n```\n")
            where = f" full output: {fname} in the shared folder"
        except Exception as e:
            where = f" (result file upload failed: {one_line(str(e), 120)})"
        summary = out if len(out) <= SUMMARY_CELL else out[:SUMMARY_CELL] + " ..."
        b.board.set(tid, row=r.rownum, status="done" if ok else "blocked", result=f"{summary}\n---{where}")
        self.log(f"{tid}: {r.title.strip()} -> {'answered from the allow-list' if ok else 'job failed'}")
        return 1

    # loop -------------------------------------------------------------------------------------------------------
    def tick(self) -> int:
        self.housekeeping()
        try:
            n = self.cycle()
        except Exception as e:
            self.log(f"cycle error: {type(e).__name__}: {e}")
            n = 0
        if n <= 0:
            if self.b.clock.now() - self._last_beat >= HEARTBEAT_EVERY:
                # A failed read used to look exactly like an empty board ("all clear") and hid an outage for hours.
                self.log("heartbeat: BLIND, board unreadable, rows may be waiting" if n < 0
                         else "heartbeat: idle, board clear")
                self._last_beat = self.b.clock.now()
        return n

    def run(self, interval: int, once: bool = False) -> int:
        lock = self.cfg.state_dir / "watch.lock"
        if not once and not acquire_lock(lock):
            self.log(f"another watcher is already running (pid {lock.read_text().strip()}), exiting")
            return 0
        self.log(f"watcher up (interval {interval} s, pid {os.getpid()}){', single cycle' if once else ''}")
        try:
            while True:
                n = self.tick()
                if n > 0:
                    self.log(f"cycle handled {n} row(s)")
                if once:
                    return 0
                time.sleep(interval)
        except KeyboardInterrupt:
            self.log("watcher stopped")
            return 0
