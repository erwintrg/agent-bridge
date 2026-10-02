"""REPLY rows resume the SAME session; replies to busy, unknown or finished sessions are routed correctly."""
INVOICES = "Find the unpaid invoices and draft payment reminders"


def test_ask_then_reply_resumes_the_same_session(env):
    env.post(INVOICES)                                       # T-0001
    env.cycle()
    s1 = env.b.sessions.all()["T-0001"]
    assert env.row(2).status == "needs-agent"
    assert 'REPLY T-0001 <your answer>' in env.row(2).result
    env.post("REPLY T-0001 skip Acme Example, they paid by phone")   # T-0002
    env.cycle()
    s2 = env.b.sessions.all()["T-0001"]
    assert s2["session"] == s1["session"]                    # same conversation, not a new one
    assert s2["turns"] == 2
    assert env.row(2).status == "needs-human"                 # the approval gate
    assert "skip Acme Example, they paid by phone" in env.row(2).result
    assert env.row(3).status == "done"
    assert env.row(3).result.startswith("T-0002: resumed T-0001")


def test_reply_detail_is_part_of_the_answer(env):
    env.post(INVOICES)
    env.cycle()
    env.post("REPLY T-0001 skip one", detail="Acme Example paid by phone on Monday")
    env.cycle()
    reply_file = env.cfg.state_dir / "sessions" / "T-0001-reply-T-0002.md"
    assert reply_file.read_text() == "skip one\n\nAcme Example paid by phone on Monday"


def test_reply_to_an_unknown_row_is_blocked(env):
    env.post("REPLY T-0099 yes")
    env.cycle()
    assert env.row(2).status == "blocked"
    assert "no local session for T-0099" in env.row(2).result


def test_reply_while_the_session_is_mid_turn_is_held(env):
    env.post(INVOICES)
    env.cycle()
    # pretend turn 2 is still running in its own unit
    env.b.sessions._update("T-0001", status="running", handle="unit:busy", turn_started=env.clock.now())
    env.b.sessions.launcher.alive = lambda handle: handle == "unit:busy"
    env.post("REPLY T-0001 skip Acme Example")
    env.cycle()
    assert env.row(3).status == "open"                        # held, retried next cycle
    env.b.sessions._update("T-0001", status="needs-agent", handle=None)
    env.cycle()
    assert env.row(3).status == "done"


def test_reply_to_a_done_row_is_a_follow_up(env):
    env.post("Summarize today's meeting notes in 3 bullets")
    env.cycle()
    assert env.row(2).status == "done"
    env.post("REPLY T-0001 and who owns bullet 2?")
    env.cycle()
    assert env.b.sessions.all()["T-0001"]["turns"] == 2


def test_member_request_wakes_the_chief_of_staff_with_a_route(env):
    env.post("Summarize today's meeting notes in 3 bullets", by="job-lane")
    env.cycle()
    assert env.wakes()[-1].split(": ", 1)[1].startswith("[for job-lane] PC reply on T-0001")


def test_session_slots_are_capped(env):
    env.b.sessions._update("T-0050", status="running", handle="unit:a", turn_started=env.clock.now())
    env.b.sessions._update("T-0051", status="running", handle="unit:b", turn_started=env.clock.now())
    env.b.sessions.launcher.alive = lambda handle: handle in ("unit:a", "unit:b")
    env.post("Summarize today's meeting notes in 3 bullets")
    env.cycle()
    assert env.row(2).status == "open"                        # waits for a free slot


def test_a_vanished_turn_is_marked_crashed(env):
    env.post(INVOICES)
    env.cycle()
    env.b.sessions._update("T-0001", status="running", handle="unit:gone", turn_started=env.clock.now())
    env.clock.advance(300)
    assert env.b.sessions.running() == []
    assert env.row(2).status == "blocked"
    assert "stopped unexpectedly" in env.row(2).result


def test_a_resume_also_waits_for_a_free_slot(env):
    env.post(INVOICES)
    env.cycle()                                               # T-0001 waits for an answer
    env.b.sessions._update("T-0050", status="running", handle="unit:a", turn_started=env.clock.now())
    env.b.sessions._update("T-0051", status="running", handle="unit:b", turn_started=env.clock.now())
    env.b.sessions.launcher.alive = lambda handle: handle in ("unit:a", "unit:b")
    env.post("REPLY T-0001 skip Acme Example")
    env.cycle()
    assert env.row(3).status == "open"                        # both slots busy: the reply waits
    env.b.sessions._update("T-0050", status="done")
    env.cycle()
    assert env.row(3).status == "done"
    assert env.b.sessions.all()["T-0001"]["turns"] == 2
