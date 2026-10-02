"""Settings from the environment plus an optional .env file (a tiny KEY=VALUE parser, no dependency).

The environment wins over the file. Relative paths in the file resolve against the file's own folder, so the
watcher and the turn processes it launches (which start with a different environment) see the same settings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = Path(path).read_text("utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        out[key] = val
    return out


def load_env(env_file: str | Path | None = None) -> dict[str, str]:
    path = Path(env_file or os.environ.get("BRIDGE_ENV_FILE") or ".env").expanduser().resolve()
    merged = parse_env_file(path)
    merged.update(os.environ)
    merged["_ENV_FILE"] = str(path)
    return merged


def _path(value: str, base: Path) -> Path:
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else (base / p)


@dataclass
class Config:
    state_dir: Path
    board: str = "json"                     # json | sqlite | sheets
    board_path: Path | None = None
    sheet_id: str = ""
    sheet_tab: str = "Tasks"
    store: str = "local"                    # local | drive
    shared_dir: Path | None = None
    drive_folder_id: str = ""
    wake: str = "log"                       # log | github
    wake_log: Path | None = None
    wake_gap: int = 120
    wake_github_repo: str = ""
    wake_github_base: str = "main"
    runner: str = "mock"                    # mock | claude
    mock_script: Path | None = None
    claude_bin: str = "claude"
    claude_model: str = ""
    claude_permission_mode: str = "acceptEdits"
    claude_allowed_tools: str = ""
    claude_workdir: Path = Path(".")
    turn_timeout: int = 45 * 60
    launcher: str = "inline"                # inline | subprocess | systemd
    max_sessions: int = 2
    local_owner: str = "PC"
    agent_name: str = "AGENT"
    tz: str = "UTC"
    interval: int = 45
    jobs_file: Path | None = None
    pulse_every: int = 180
    sync_gap: int = 30 * 60
    transcripts_dir: Path | None = None
    memory_dir: Path | None = None
    watch_dirs: list[Path] = field(default_factory=list)
    system_checks: bool = True
    env_file: Path | None = None
    env: dict = field(default_factory=dict, repr=False)   # raw values, only read by the Google backends

    @classmethod
    def from_env(cls, env: Mapping[str, str], base: Path | None = None) -> "Config":
        base = Path(base or Path.cwd())

        def g(key: str, default: str = "") -> str:
            return (env.get(key) or default).strip()

        def opt(key: str) -> Path | None:
            return _path(g(key), base) if g(key) else None

        state = _path(g("BRIDGE_STATE_DIR", "state"), base)
        return cls(
            state_dir=state,
            board=g("BRIDGE_BOARD", "json").lower(),
            board_path=opt("BRIDGE_BOARD_PATH"),
            sheet_id=g("SHEET_ID"),
            sheet_tab=g("SHEET_TAB", "Tasks"),
            store=g("BRIDGE_STORE", "local").lower(),
            shared_dir=opt("BRIDGE_SHARED_DIR"),
            drive_folder_id=g("DRIVE_FOLDER_ID"),
            wake=g("BRIDGE_WAKE", "log").lower(),
            wake_log=opt("WAKE_LOG"),
            wake_gap=int(g("WAKE_GAP_SECONDS", "120")),
            wake_github_repo=g("WAKE_GITHUB_REPO"),
            wake_github_base=g("WAKE_GITHUB_BASE", "main"),
            runner=g("BRIDGE_RUNNER", "mock").lower(),
            mock_script=opt("MOCK_SCRIPT"),
            claude_bin=g("CLAUDE_BIN", "claude"),
            claude_model=g("CLAUDE_MODEL"),
            claude_permission_mode=g("CLAUDE_PERMISSION_MODE", "acceptEdits"),
            claude_allowed_tools=g("CLAUDE_ALLOWED_TOOLS"),
            claude_workdir=_path(g("CLAUDE_WORKDIR", "."), base),
            turn_timeout=int(g("CLAUDE_TURN_TIMEOUT", "2700")),
            launcher=g("BRIDGE_LAUNCHER", "subprocess").lower(),
            max_sessions=int(g("BRIDGE_MAX_SESSIONS", "2")),
            local_owner=g("BRIDGE_LOCAL_OWNER", "PC"),
            agent_name=g("BRIDGE_AGENT_NAME", "AGENT"),
            tz=g("BRIDGE_TZ", "UTC"),
            interval=int(g("BRIDGE_INTERVAL", "45")),
            jobs_file=opt("BRIDGE_JOBS_FILE"),
            pulse_every=int(g("PULSE_EVERY_SECONDS", "180")),
            sync_gap=int(g("SYNC_GAP_SECONDS", "1800")),
            transcripts_dir=opt("PULSE_TRANSCRIPTS_DIR"),
            memory_dir=opt("PULSE_MEMORY_DIR"),
            watch_dirs=[_path(p.strip(), base) for p in g("PULSE_WATCH_DIRS").split(",") if p.strip()],
            system_checks=g("PULSE_SYSTEM_CHECKS", "1").lower() not in ("0", "false", "no"),
            env_file=Path(env["_ENV_FILE"]) if env.get("_ENV_FILE") else None,
            env=dict(env),
        )
