"""The pulse (PC -> agent) and the fact sync (agent -> PC), plus task drops."""
import json
import os

from conftest import START


def touch(path, ts, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (ts, ts))


def test_pulse_redacts_prompts_and_lists_file_names_only(make_env, tmp_path):
    secret = "ghp_" + "Zz9" * 8
    t = "2026-10-01T08:30:00Z"
    lines = [{"type": "user", "timestamp": t, "message": {"content": f"deploy with {secret} please"}},
             {"type": "assistant", "timestamp": t, "message": {"content": [{"type": "text", "text": "Deployed."}]}}]
    touch(tmp_path / "tr" / "s.jsonl", START - 600, "\n".join(json.dumps(x) for x in lines))
    touch(tmp_path / "ws" / "notes" / "plan.md", START - 3600, "private content")
    touch(tmp_path / "ws" / "credentials.json", START - 3600)
    touch(tmp_path / "ws" / "old.md", START - 9 * 86400)
    env = make_env(transcripts_dir=tmp_path / "tr", watch_dirs=[tmp_path / "ws"])
    env.b.pulse.run()
    body = (env.cfg.shared_dir / "pc-status.md").read_text()
    assert secret not in body and "deploy with [redacted] please" in body
    assert "plan.md" in body and "private content" not in body
    assert "credentials.json" not in body and "old.md" not in body


def test_pulse_uploads_only_when_content_changes(env):
    assert "pc-status.md uploaded" in env.b.pulse.run()
    env.clock.advance(60)                                 # only the timestamp line differs
    assert "uploaded" not in env.b.pulse.run()


def test_changed_agent_status_adds_one_sync_row(env):
    env.b.store.write("agent-status.md", "## Deadlines\n- 2026-10-07 17:00 proposal\n")
    assert "SYNC row T-0001 added" in env.b.pulse.run()
    env.b.store.write("agent-status.md", "## Deadlines\n- 2026-10-08 12:00 another\n")
    env.clock.advance(60)
    assert "SYNC due" in env.b.pulse.run()                # inside the sync gap: no second row yet
    titles = [r.title for r in env.b.board.rows()]
    assert len(titles) == 1 and titles[0].startswith("SYNC agent-status")
    assert (env.cfg.state_dir / "from-agent" / "agent-status.prev.md").exists()


def test_memory_change_wakes_the_agent_after_the_baseline(make_env, tmp_path):
    touch(tmp_path / "mem" / "user_hours.md", START - 86400, "description: hours\n")
    env = make_env(memory_dir=tmp_path / "mem")
    env.b.pulse.run()                                     # first run: baseline, no wake-up
    assert env.wakes() == []
    touch(tmp_path / "mem" / "feedback_drafts.md", START, "description: drafts first\n")
    assert "memory changed (feedback_drafts)" in env.b.pulse.run()
    assert "PC memory changed: feedback_drafts" in env.wakes()[-1]


def test_task_drop_becomes_a_row_and_a_bad_drop_stays(env):
    env.b.store.write("task-1.json", json.dumps({"title": "Summarize today's meeting notes", "requested_by": "AGENT"}))
    env.b.store.write("task-2.json", json.dumps({"note": "no title here"}))
    env.b.store.write("task-3.json", "{not json")
    env.watcher.ingest()
    assert [r.title for r in env.b.board.rows()] == ["Summarize today's meeting notes"]
    assert env.b.store.list("task-") == ["task-2.json", "task-3.json"]
