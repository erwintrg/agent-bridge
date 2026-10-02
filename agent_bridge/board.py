"""The shared task board. Backends only read, append and write cells; the targeting rules live in `Board`.

Local backends: `JsonBoard` (default, one file) and `SqliteBoard`. Google Sheets: `google_backends.SheetsBoard`."""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from .clock import Clock
from .rows import COLUMNS, FIRST_ROW, Row, next_id
from .util import file_lock, read_json, write_json


class BoardError(Exception):
    pass


class NoSuchRow(BoardError):
    pass


class RowMismatch(BoardError):
    pass


class Board:
    def __init__(self, clock: Clock):
        self.clock = clock

    # backend hooks ---------------------------------------------------------------------------------------------
    def _read(self) -> list[dict]:
        raise NotImplementedError

    def _append(self, values: dict) -> None:
        raise NotImplementedError

    def _write(self, rownum: int, changes: dict) -> None:
        raise NotImplementedError

    def _locked(self):
        return contextlib.nullcontext()

    # public API ------------------------------------------------------------------------------------------------
    def rows(self) -> list[Row]:
        return [Row.from_values(i + FIRST_ROW, v) for i, v in enumerate(self._read())]

    def add(self, *, owner: str, title: str, detail: str = "", requested_by: str = "", status: str = "open",
            tid: str | None = None) -> str:
        """Append a row. tid=None takes the next free id; tid='' writes a blank id (what a careless direct
        append does); any other value is written as given (what a retried write does)."""
        with self._locked():
            if tid is None:
                tid = next_id(v.get("id", "") for v in self._read())
            now = self.clock.stamp()
            self._append({"id": tid, "created": now, "requested_by": requested_by, "owner": owner, "title": title,
                          "detail": detail, "status": status, "result": "", "updated": now})
        return tid

    def set(self, tid: str, *, row: int | None = None, status: str | None = None, result: str | None = None,
            new_id: bool = False) -> str:
        """Update one row and return its id (the new one with new_id=True).

        With row=N the row must still hold `tid` ('-' means a blank id). That is how duplicates and blank ids are
        handled: by row number, never by "the first row with this id", which only ever reaches the first copy."""
        want = "" if tid == "-" else tid.strip()
        with self._locked():
            current = self._read()
            if row is not None:
                i = row - FIRST_ROW
                if i < 0 or i >= len(current):
                    raise NoSuchRow(f"row {row} does not exist")
                if (current[i].get("id") or "").strip() != want:
                    raise RowMismatch(f"row {row} does not hold id {tid}")
                rownum = row
            else:
                hits = [i for i, v in enumerate(current) if want and (v.get("id") or "").strip() == want]
                if not hits:
                    raise NoSuchRow(f"no row with id {tid}")
                rownum = hits[0] + FIRST_ROW
            changes: dict[str, str] = {}
            out = want
            if new_id:
                out = next_id(v.get("id", "") for v in current)
                changes["id"] = out
            if status is not None:
                changes["status"] = status
            if result is not None:
                changes["result"] = result
            changes["updated"] = self.clock.stamp()
            self._write(rownum, changes)
        return out


class JsonBoard(Board):
    """Default local board: one JSON file, a lock file for writers, an atomic rename on every write."""

    def __init__(self, path: Path, clock: Clock):
        super().__init__(clock)
        self.path = Path(path)

    def _locked(self):
        return file_lock(self.path.with_name(self.path.name + ".lock"))

    def _read(self) -> list[dict]:
        return list(read_json(self.path, {}).get("rows", []))

    def _save(self, rows: list[dict]) -> None:
        write_json(self.path, {"columns": COLUMNS, "rows": rows})

    def _append(self, values: dict) -> None:
        rows = self._read()
        rows.append(values)
        self._save(rows)

    def _write(self, rownum: int, changes: dict) -> None:
        rows = self._read()
        rows[rownum - FIRST_ROW].update(changes)
        self._save(rows)


class SqliteBoard(Board):
    """Local board in SQLite. Rows are never deleted, so the position in insertion order is the row number."""

    def __init__(self, path: Path, clock: Clock):
        super().__init__(clock)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        cols = ", ".join(f"{c} TEXT NOT NULL DEFAULT ''" for c in COLUMNS)
        self._exec(f"CREATE TABLE IF NOT EXISTS tasks (pos INTEGER PRIMARY KEY AUTOINCREMENT, {cols})")

    def _exec(self, sql: str, args=()) -> list[tuple]:
        con = sqlite3.connect(self.path, timeout=30)
        try:
            with con:
                return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def _locked(self):
        return file_lock(self.path.with_name(self.path.name + ".lock"))

    def _read(self) -> list[dict]:
        return [dict(zip(COLUMNS, r)) for r in self._exec(f"SELECT {', '.join(COLUMNS)} FROM tasks ORDER BY pos")]

    def _append(self, values: dict) -> None:
        marks = ", ".join("?" for _ in COLUMNS)
        self._exec(f"INSERT INTO tasks ({', '.join(COLUMNS)}) VALUES ({marks})", [values.get(c, "") for c in COLUMNS])

    def _write(self, rownum: int, changes: dict) -> None:
        bad = set(changes) - set(COLUMNS)
        if bad:
            raise BoardError(f"unknown columns {sorted(bad)}")
        pos = self._exec("SELECT pos FROM tasks ORDER BY pos LIMIT 1 OFFSET ?", (rownum - FIRST_ROW,))
        if not pos:
            raise NoSuchRow(f"row {rownum} does not exist")
        sets = ", ".join(f"{k} = ?" for k in changes)
        self._exec(f"UPDATE tasks SET {sets} WHERE pos = ?", [*changes.values(), pos[0][0]])
