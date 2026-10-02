"""Wire the pluggable parts together from a Config."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .board import Board, JsonBoard, SqliteBoard
from .clock import Clock
from .config import Config
from .jobs import Job, JobRegistry
from .launchers import InlineLauncher, SubprocessLauncher, SystemdLauncher
from .pulse import PULSE_FILE, Pulse
from .runners import ClaudeCliRunner, MockRunner
from .sessions import SessionManager
from .store import LocalFolderStore, Store
from .util import one_line, read_json
from .wake import GitHubPRWaker, LogWaker, RateLimitedWaker


class FileLog:
    """Log line = local timestamp + message, appended to state/watch.log and echoed to stdout."""

    def __init__(self, path: Path, clock: Clock, echo: bool = True):
        self.path, self.clock, self.echo = Path(path), clock, echo

    def __call__(self, msg: str) -> None:
        line = f"{self.clock.dt().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.echo:
            try:
                print(line, flush=True)
            except (OSError, ValueError):
                pass   # detached, no usable stdout: the file is the log


@dataclass
class Bridge:
    cfg: Config
    clock: Clock
    board: Board
    store: Store
    waker: RateLimitedWaker
    jobs: JobRegistry
    log: Callable[[str], None]
    sessions: SessionManager | None = None
    pulse: Pulse | None = None


def _google(cfg: Config):
    from .google_backends import GoogleApi
    return GoogleApi(cfg.env)


def make_board(cfg: Config, clock: Clock) -> Board:
    if cfg.board == "json":
        return JsonBoard(cfg.board_path or cfg.state_dir / "board.json", clock)
    if cfg.board == "sqlite":
        return SqliteBoard(cfg.board_path or cfg.state_dir / "board.sqlite", clock)
    if cfg.board == "sheets":
        from .google_backends import SheetsBoard
        return SheetsBoard(_google(cfg), cfg.sheet_id, cfg.sheet_tab, clock)
    raise ValueError(f"unknown BRIDGE_BOARD {cfg.board!r} (json, sqlite or sheets)")


def make_store(cfg: Config) -> Store:
    if cfg.store == "local":
        return LocalFolderStore(cfg.shared_dir or cfg.state_dir / "shared")
    if cfg.store == "drive":
        from .google_backends import DriveFolderStore
        return DriveFolderStore(_google(cfg), cfg.drive_folder_id)
    raise ValueError(f"unknown BRIDGE_STORE {cfg.store!r} (local or drive)")


def make_waker(cfg: Config, clock: Clock) -> RateLimitedWaker:
    if cfg.wake == "log":
        backend = LogWaker(cfg.wake_log or cfg.state_dir / "wake.log", clock, cfg.agent_name)
    elif cfg.wake == "github":
        backend = GitHubPRWaker(cfg.wake_github_repo, cfg.wake_github_base, cfg.agent_name, clock)
    else:
        raise ValueError(f"unknown BRIDGE_WAKE {cfg.wake!r} (log or github)")
    return RateLimitedWaker(backend, cfg.state_dir, clock, cfg.wake_gap)


def make_runner(cfg: Config):
    if cfg.runner == "mock":
        script = cfg.mock_script or Path(__file__).resolve().parent.parent / "fixtures" / "mock_agent.json"
        return MockRunner(script, cfg.state_dir / "mock_sessions.json")
    if cfg.runner == "claude":
        return ClaudeCliRunner(cfg.claude_bin, cfg.claude_model, cfg.claude_permission_mode, cfg.claude_allowed_tools,
                               cfg.claude_workdir, cfg.turn_timeout)
    raise ValueError(f"unknown BRIDGE_RUNNER {cfg.runner!r} (mock or claude)")


def open_rows_text(b: Bridge) -> str:
    hot = ("open", "in-progress", "needs-agent", "needs-human", "blocked")
    rows = [r for r in b.board.rows() if r.status in hot]
    return "\n".join(f"{r.id or '(no id)'} [{r.status}] {r.owner}: {one_line(r.title, 100)}" for r in rows) \
        or "nothing open"


def build(cfg: Config, *, clock: Clock | None = None, log: Callable[[str], None] | None = None) -> Bridge:
    clock = clock or Clock(cfg.tz)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    log = log or FileLog(cfg.state_dir / "watch.log", clock)
    b = Bridge(cfg=cfg, clock=clock, board=make_board(cfg, clock), store=make_store(cfg),
               waker=make_waker(cfg, clock), jobs=JobRegistry(), log=log)
    b.sessions = SessionManager(board=b.board, waker=b.waker, runner=make_runner(cfg), store=b.store, clock=clock,
                                state_dir=cfg.state_dir, agent_name=cfg.agent_name, local_owner=cfg.local_owner,
                                max_running=cfg.max_sessions, log=log)
    if cfg.launcher == "inline":
        b.sessions.launcher = InlineLauncher(b.sessions.dispatch)
    elif cfg.launcher == "subprocess":
        b.sessions.launcher = SubprocessLauncher(cfg.env_file)
    elif cfg.launcher == "systemd":
        b.sessions.launcher = SystemdLauncher(cfg.env_file)
    else:
        raise ValueError(f"unknown BRIDGE_LAUNCHER {cfg.launcher!r} (inline, subprocess or systemd)")
    b.pulse = Pulse(cfg=cfg, clock=clock, board=b.board, store=b.store, waker=b.waker, sessions=b.sessions, log=log)
    b.sessions.agent_status_file = b.pulse.agent_status_path
    # built-in read-only jobs, then the user's allow-list
    b.jobs.add(Job("board", func=lambda p: open_rows_text(b), about="open rows on the board"))
    b.jobs.add(Job("sessions", func=lambda p: b.sessions.status_text(), about="local sessions and their state"))
    b.jobs.add(Job("pulse", func=lambda p: (cfg.state_dir / PULSE_FILE).read_text("utf-8")
                   if (cfg.state_dir / PULSE_FILE).exists() else "no pulse built yet", about="the latest PC pulse"))
    b.jobs.add(Job("time", func=lambda p: clock.dt().strftime("%a %d.%m.%Y %H:%M %Z"), about="the PC's clock"))
    if cfg.jobs_file:
        b.jobs.load_file(cfg.jobs_file)
    return b


def context_text(b: Bridge, limit: int = 3500) -> str:
    """Session-start context for interactive local sessions (Claude Code SessionStart hook): the cloud agent's
    status file and the board rows that are waiting. Local files only, so it is instant."""
    out = [f"## Agent link (the cloud agent, board name {b.cfg.agent_name}, is the user's chief of staff)",
           f"Now: {b.clock.dt().strftime('%a %d.%m.%Y %H:%M %Z')}"]
    f = b.pulse.agent_status_path
    if f.exists():
        when = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m %H:%M")
        out += [f"The agent's status file (pulled {when}; newest facts win over memory, verify before acting):",
                f.read_text("utf-8", errors="replace")[:limit]]
    else:
        out.append("The agent has not written agent-status.md yet.")
    try:
        cache = read_json(b.cfg.state_dir / "board_cache.json", [])
        hot = [r for r in cache if r.get("status") in ("needs-human", "needs-agent", "in-progress")]
        if hot:
            out.append("Open board rows (needs-human / needs-agent / in-progress):")
            out += [f"- {r.get('id')} [{r.get('status')}] {r.get('owner')}: {one_line(r.get('title', ''), 110)}"
                    for r in hot[-8:]]
    except Exception:
        pass
    out.append("To reach the agent: python -m agent_bridge notify --title \"...\" (adds a row and wakes it).")
    return "\n".join(out)
