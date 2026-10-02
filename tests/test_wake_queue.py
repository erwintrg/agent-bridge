"""Rate-limited wake-ups: one per gap, the rest queued and flushed later as ONE wake-up, never lost."""
import pytest

from agent_bridge.clock import SimClock
from agent_bridge.wake import RateLimitedWaker
from conftest import START


class Recorder:
    def __init__(self, fail: bool = False):
        self.sent, self.fail = [], fail

    def send(self, reason, sender):
        if self.fail:
            raise RuntimeError("PR API down")
        self.sent.append(reason)
        return "sent"


@pytest.fixture
def setup(tmp_path):
    clock, rec = SimClock(START), Recorder()
    return clock, rec, RateLimitedWaker(rec, tmp_path, clock, gap=120)


def test_first_wake_goes_out_then_the_gap_queues(setup):
    clock, rec, w = setup
    assert w.wake("a") == "sent"
    clock.advance(30)
    assert w.wake("b").startswith("queued")
    assert w.wake("c").startswith("queued")
    assert rec.sent == ["a"] and w.pending() == ["b", "c"]


def test_flush_waits_for_the_gap_then_sends_one_merged_wake(setup):
    clock, rec, w = setup
    w.wake("a")
    clock.advance(30)
    w.wake("b")
    w.wake("c")
    assert w.flush() == ""                              # still inside the gap
    clock.advance(90)
    assert w.flush().startswith("2 queued wake-up(s) sent as one")
    assert rec.sent == ["a", "2 updates: b || c"]
    assert w.pending() == []
    assert w.flush() == ""                              # nothing left


def test_single_queued_reason_is_sent_as_is(setup):
    clock, rec, w = setup
    w.wake("a")
    w.wake("b")
    clock.advance(120)
    w.flush()
    assert rec.sent == ["a", "b"]


def test_force_skips_the_gap(setup):
    clock, rec, w = setup
    w.wake("a")
    assert w.wake("urgent", force=True) == "sent"
    assert rec.sent == ["a", "urgent"]


def test_a_failed_send_is_queued_and_retried_after_the_gap(tmp_path):
    clock, rec = SimClock(START), Recorder(fail=True)
    w = RateLimitedWaker(rec, tmp_path, clock, gap=120)
    assert "queued for retry" in w.wake("a")
    assert w.pending() == ["a"]
    rec.fail = False
    assert w.flush() == ""                              # a failure also starts the gap: no retry storm
    clock.advance(120)
    w.flush()
    assert rec.sent == ["a"] and w.pending() == []


def test_long_reasons_are_one_line_and_capped(setup):
    clock, rec, w = setup
    w.wake("line one\nline two " + "x" * 500)
    assert "\n" not in rec.sent[0] and len(rec.sent[0]) <= 300
