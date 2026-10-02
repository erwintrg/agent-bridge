"""Row parsing: the small pure functions every routing decision rests on."""
from agent_bridge.rows import for_member, next_id, outcome, parse_job_request, parse_reply, title_key


def test_job_request_with_and_without_param():
    assert parse_job_request("GET calendar 3") == ("calendar", "3")
    assert parse_job_request("  get Board ") == ("board", None)
    assert parse_job_request("fetch inbox 2") == ("inbox", "2")


def test_plain_words_are_not_a_job():
    assert parse_job_request("Please GET the calendar for me") is None
    assert parse_job_request("Find the unpaid invoices") is None
    # more than one parameter is not a job request: the row becomes a normal session request
    assert parse_job_request("GET calendar 3 days") is None


def test_injection_lands_in_the_param_where_the_validator_stops_it():
    assert parse_job_request("GET calendar ;rm") == ("calendar", ";rm")


def test_reply_parsing():
    assert parse_reply("REPLY T-0042 yes, go ahead") == ("T-0042", "yes, go ahead")
    assert parse_reply("reply t-7: no thanks") == ("T-7", "no thanks")
    assert parse_reply("REPLY T-0042 - skip Acme") == ("T-0042", "skip Acme")
    assert parse_reply("REPLY T-0042") == ("T-0042", "")
    assert parse_reply("REPLY T-0042 line one\nline two") == ("T-0042", "line one\nline two")


def test_not_a_reply():
    assert parse_reply("REPLYING T-0042 yes") is None
    assert parse_reply("Re: REPLY T-0042 yes") is None
    assert parse_reply("REPLY to T-0042") is None
    assert parse_reply("") is None


def test_title_key_ignores_case_punctuation_and_spacing():
    assert title_key("Find  the unpaid invoices!") == title_key("find the unpaid invoices")
    assert title_key("Find the unpaid invoices") != title_key("Find the paid invoices")


def test_outcome_maps_the_last_message_to_a_status():
    assert outcome("ASK AGENT: which month?", True) == "needs-agent"
    assert outcome("  ask agent: which month?", True) == "needs-agent"
    assert outcome("NEEDS HUMAN: send 2 emails?", True) == "needs-human"
    assert outcome("Done. 3 files.", True) == "done"
    assert outcome("ASK AGENT: anything", False) == "blocked"
    assert outcome("ASK COS: which month?", True, agent_name="cos") == "needs-agent"
    assert outcome("ASK AGENT: which month?", True, agent_name="cos") == "done"


def test_next_id_skips_blank_and_odd_ids():
    assert next_id([]) == "T-0001"
    assert next_id(["T-0001", "", "T-0010", "x", "  T-0003 "]) == "T-0011"


def test_wake_reason_routing_one_front_door():
    main = {"AGENT", "PC", "bridge-sync"}
    assert for_member("job-lane", "PC reply on T-1", main) == "[for job-lane] PC reply on T-1"
    assert for_member("@job-lane", "x", main) == "[for job-lane] x"
    assert for_member("agent", "x", main) == "x"
    assert for_member("", "x", main) == "x"
