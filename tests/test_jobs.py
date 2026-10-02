"""The GET allow-list: fixed jobs, one validated parameter, no shell."""
from agent_bridge.jobs import JobRegistry
from conftest import ROOT


def registry():
    r = JobRegistry()
    r.load_file(ROOT / "fixtures" / "jobs.json")
    return r


def test_unknown_job_and_bad_params_are_refused():
    r = registry()
    assert r.check("passwords", None) == "unknown job 'passwords'"
    assert r.check("calendar", ";rm") == "bad parameter for 'calendar'"
    assert r.check("calendar", "123") == "bad parameter for 'calendar'"
    assert r.check("calendar", "3") is None
    assert r.check("calendar", None) is None


def test_job_runs_without_a_shell_and_uses_the_default():
    ok, out = registry().run("calendar", None)
    assert ok and out.startswith("Calendar, next 7 days")


def test_get_row_end_to_end(env):
    env.post("GET calendar 2")
    env.post("GET passwords")
    env.cycle()
    assert env.row(2).status == "done" and "Calendar, next 2 days" in env.row(2).result
    assert (env.cfg.shared_dir / "result-T-0001.md").exists()
    assert env.row(3).status == "blocked" and "known jobs: GET board, GET calendar" in env.row(3).result
