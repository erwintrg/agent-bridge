"""Board backends: ids, row-number targeting and the safety check that the row still holds the id."""
import pytest

from agent_bridge.board import JsonBoard, NoSuchRow, RowMismatch, SqliteBoard
from agent_bridge.clock import SimClock
from conftest import START


@pytest.fixture(params=["json", "sqlite"])
def board(request, tmp_path):
    clock = SimClock(START)
    cls = JsonBoard if request.param == "json" else SqliteBoard
    return cls(tmp_path / f"board.{request.param}", clock), clock


def test_add_assigns_ids_and_stamps(board):
    b, _ = board
    assert b.add(owner="PC", title="a") == "T-0001"
    assert b.add(owner="PC", title="b") == "T-0002"
    r = b.rows()[0]
    assert (r.rownum, r.status, r.created) == (2, "open", "2026-10-01 09:00")


def test_blank_and_explicit_ids(board):
    b, _ = board
    b.add(owner="PC", title="a", tid="")
    b.add(owner="PC", title="b", tid="T-0007")
    assert [r.id for r in b.rows()] == ["", "T-0007"]
    assert b.add(owner="PC", title="c") == "T-0008"


def test_set_by_row_number_targets_the_exact_copy(board):
    b, clock = board
    b.add(owner="PC", title="x")                # row 2, T-0001
    b.add(owner="PC", title="x", tid="T-0001")  # row 3, same id
    clock.advance(60)
    b.set("T-0001", row=3, status="done", result="duplicate")
    rows = b.rows()
    assert rows[0].status == "open" and rows[1].status == "done"
    assert rows[1].updated == "2026-10-01 09:01"


def test_set_by_id_reaches_the_first_copy(board):
    b, _ = board
    b.add(owner="PC", title="x")
    b.add(owner="PC", title="x", tid="T-0001")
    b.set("T-0001", status="in-progress")
    assert [r.status for r in b.rows()] == ["in-progress", "open"]


def test_row_must_still_hold_the_id(board):
    b, _ = board
    b.add(owner="PC", title="x")
    with pytest.raises(RowMismatch):
        b.set("T-0002", row=2, status="done")
    with pytest.raises(NoSuchRow):
        b.set("T-0001", row=9, status="done")
    with pytest.raises(NoSuchRow):
        b.set("T-0042", status="done")


def test_new_id_for_a_blank_row(board):
    b, _ = board
    b.add(owner="PC", title="a")
    b.add(owner="PC", title="b", tid="")
    assert b.set("-", row=3, new_id=True) == "T-0002"
    assert b.rows()[1].id == "T-0002"
