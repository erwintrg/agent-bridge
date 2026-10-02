import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_bridge.app import build  # noqa: E402
from agent_bridge.clock import SimClock  # noqa: E402
from agent_bridge.config import Config  # noqa: E402
from agent_bridge.watcher import Watcher  # noqa: E402

START = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).timestamp()


class Env:
    """A bridge on local backends, simulated time, the scripted mock agent and the inline launcher."""

    def __init__(self, tmp_path: Path, **overrides):
        self.clock = SimClock(START)
        self.logs: list[str] = []
        cfg = dict(state_dir=tmp_path / "state", board="json", board_path=tmp_path / "board.json", store="local",
                   shared_dir=tmp_path / "shared", wake="log", wake_log=tmp_path / "wake.log", wake_gap=120,
                   runner="mock", mock_script=ROOT / "fixtures" / "mock_agent.json", launcher="inline",
                   jobs_file=ROOT / "fixtures" / "jobs.json", system_checks=False, pulse_every=10 ** 9)
        cfg.update(overrides)
        self.cfg = Config(**cfg)
        self.b = build(self.cfg, clock=self.clock, log=self.logs.append)
        self.watcher = Watcher(self.b)

    def post(self, title: str, tid=None, detail: str = "", owner: str = "PC", by: str = "AGENT") -> str:
        return self.b.board.add(owner=owner, title=title, detail=detail, requested_by=by, tid=tid)

    def cycle(self) -> int:
        n = self.watcher.cycle()
        self.clock.advance(45)
        return n

    def row(self, rownum: int):
        return next(r for r in self.b.board.rows() if r.rownum == rownum)

    def wakes(self) -> list[str]:
        p = self.cfg.wake_log
        return p.read_text("utf-8").splitlines() if p.exists() else []


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


@pytest.fixture
def make_env(tmp_path):
    return lambda **kw: Env(tmp_path, **kw)
