"""Duplicate and blank-id guards. Every close is aimed at a ROW NUMBER, never at "the first row with this id"."""
INVOICES = "Find the unpaid invoices and draft payment reminders"


def test_same_request_written_twice_runs_once(env):
    env.post(INVOICES)                       # row 2, T-0001
    env.post(INVOICES.upper() + "!", tid="")  # row 3: same request, no id, different case and punctuation
    env.cycle()
    assert env.row(2).status == "needs-agent"           # the first copy started its session
    assert env.row(3).status == "done"
    assert env.row(3).result == "duplicate of T-0001; not run again"
    assert env.row(3).id == ""                          # the blank-id row itself was targeted, not row 2
    assert len(env.b.sessions.all()) == 1


def test_reused_id_with_another_title_is_closed(env):
    env.post("GET calendar 2")                          # row 2, T-0001
    env.post("Summarize today's meeting notes", tid="T-0001")   # row 3: a different request under a used id
    env.cycle()
    assert env.row(2).status == "done"
    assert env.row(3).status == "done"
    assert env.row(3).result.startswith("duplicate of T-0001 (same id written twice)")


def test_retried_reply_resumes_the_session_once(env):
    env.post(INVOICES)                                  # T-0001 -> ASK AGENT
    env.cycle()
    env.post("REPLY T-0001 skip Acme Example")          # T-0002
    env.post("REPLY T-0001 skip Acme Example", tid="T-0002")    # the same write retried
    env.cycle()
    s = env.b.sessions.all()["T-0001"]
    assert s["turns"] == 2                               # resumed once, not twice
    assert env.row(4).result == "duplicate of T-0002; not run again"


def test_blank_id_row_gets_the_next_id_and_runs_next_cycle(env):
    env.post("GET calendar 2")                          # T-0001
    env.post("Summarize today's meeting notes in 3 bullets", tid="")
    env.cycle()
    row = env.row(3)
    assert row.id == "T-0002" and row.status == "open"
    env.cycle()
    assert env.row(3).status == "done"


def test_two_blank_rows_get_distinct_ids(env):
    env.post("first request", tid="")
    env.post("second request", tid="")
    env.cycle()
    assert [env.row(2).id, env.row(3).id] == ["T-0001", "T-0002"]


def test_get_rows_are_never_deduplicated(env):
    env.post("GET calendar 2")
    env.post("GET calendar 2")
    env.cycle()
    assert env.row(2).status == env.row(3).status == "done"
    assert "duplicate" not in env.row(3).result


def test_retry_after_blocked_is_not_a_duplicate(env):
    env.b.board.add(owner="PC", title=INVOICES, status="blocked")   # an earlier attempt that failed
    env.post(INVOICES)
    env.cycle()
    assert env.row(3).status == "needs-agent"


def test_same_title_after_the_window_is_a_new_request(env):
    env.post(INVOICES)
    env.cycle()
    env.clock.advance(2 * 3600)
    env.post(INVOICES)
    env.cycle()
    assert env.row(3).status == "needs-agent"
    assert len(env.b.sessions.all()) == 2


def test_rows_for_other_owners_are_left_alone(env):
    env.post("Write the weekly digest", owner="AGENT")
    env.post("Write the weekly digest", owner="AGENT", tid="")
    env.cycle()
    assert env.row(2).status == env.row(3).status == "open"
    assert env.row(3).id == ""
