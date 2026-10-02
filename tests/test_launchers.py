"""Turns started out of process: the multi-process path the systemd launcher also uses, plus the Claude CLI glue."""
import time

from agent_bridge.app import build
from agent_bridge.config import Config, load_env
from agent_bridge.runners import ClaudeCliRunner, parse_claude_json
from agent_bridge.watcher import Watcher
from conftest import ROOT


def test_subprocess_launcher_runs_the_turn_in_its_own_process(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BRIDGE_STATE_DIR=state\nBRIDGE_BOARD=json\nBRIDGE_STORE=local\nBRIDGE_WAKE=log\n"
        "BRIDGE_RUNNER=mock\nBRIDGE_LAUNCHER=subprocess\nPULSE_SYSTEM_CHECKS=0\n"
        f"MOCK_SCRIPT={ROOT / 'fixtures' / 'mock_agent.json'}\n")
    env = load_env(env_file)
    cfg = Config.from_env(env, base=tmp_path)
    b = build(cfg, log=lambda m: None)
    b.board.add(owner="PC", title="Find the unpaid invoices", requested_by="AGENT")
    Watcher(b).cycle()
    deadline = time.time() + 20
    while time.time() < deadline and b.board.rows()[0].status == "in-progress":
        time.sleep(0.2)
    row = b.board.rows()[0]
    assert row.status == "needs-agent", row.result
    assert b.sessions.all()["T-0001"]["session"] == "mock-0001"
    assert (tmp_path / "state" / "wake.log").exists()


def test_parse_claude_json():
    out = '{"type":"result","result":"Done. 3 files.","session_id":"abc-123","is_error":false}'
    r = parse_claude_json("some log line\n" + out, None)
    assert (r.text, r.session_id, r.ok) == ("Done. 3 files.", "abc-123", True)
    r = parse_claude_json('{"result":"","session_id":"abc","is_error":true}', "old")
    assert (r.session_id, r.ok) == ("abc", False)
    r = parse_claude_json("Error: not logged in", "old")
    assert (r.text, r.session_id, r.ok) == ("Error: not logged in", "old", False)


def test_claude_argv_resumes_and_restricts_tools():
    argv = ClaudeCliRunner(model="opus", allowed_tools="Read,Grep", permission_mode="default").argv("hi", "s-1")
    assert argv[:3] == ["claude", "-p", "hi"]
    assert argv[argv.index("--resume") + 1] == "s-1"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep"
    assert "--resume" not in ClaudeCliRunner().argv("hi", None)
