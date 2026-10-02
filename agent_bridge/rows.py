"""The board row and the small parsers the watcher routes on. Pure functions, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

COLUMNS = ["id", "created", "requested_by", "owner", "title", "detail", "status", "result", "updated"]
STATUSES = ("open", "in-progress", "needs-agent", "needs-human", "blocked", "done")
FIRST_ROW = 2          # row 1 is the header, exactly like a spreadsheet
ID_PREFIX = "T"


@dataclass
class Row:
    rownum: int
    id: str = ""
    created: str = ""
    requested_by: str = ""
    owner: str = ""
    title: str = ""
    detail: str = ""
    status: str = ""
    result: str = ""
    updated: str = ""

    @classmethod
    def from_values(cls, rownum: int, values: dict) -> "Row":
        return cls(rownum=rownum, **{c: str(values.get(c) or "") for c in COLUMNS})

    def values(self) -> dict:
        return {c: getattr(self, c) for c in COLUMNS}


def next_id(existing: Iterable[str]) -> str:
    """Next free id: highest number on the board plus one. Blank and odd ids are ignored."""
    n = 0
    for i in existing:
        try:
            n = max(n, int(str(i).strip().rsplit("-", 1)[-1]))
        except ValueError:
            pass
    return f"{ID_PREFIX}-{n + 1:04d}"


JOB_RE = re.compile(r"^\s*(get|run|fetch)\s+([a-z0-9-]+)\s*(\S+)?\s*$", re.I)


def parse_job_request(title: str):
    """'GET calendar 3' -> ('calendar', '3'), 'get board' -> ('board', None). None means: not a job request,
    the row is a plain-words request for a local session. Whether the job exists is the registry's call."""
    m = JOB_RE.match(title or "")
    if not m:
        return None
    return m.group(2).lower(), m.group(3)


REPLY_RE = re.compile(r"^\s*REPLY\s+(T-\d+)\b[:\s-]*(.*)$", re.I | re.S)


def parse_reply(title: str):
    """'REPLY T-0042 yes, go ahead' -> ('T-0042', 'yes, go ahead'). None if the row is not a reply."""
    m = REPLY_RE.match(title or "")
    return (m.group(1).upper(), m.group(2).strip()) if m else None


def title_key(title: str) -> str:
    """Normalised title for the duplicate guard: case, punctuation and spacing do not make a new request."""
    return re.sub(r"\W+", " ", title or "").strip().lower()


def outcome(text: str, ok: bool, agent_name: str = "AGENT") -> str:
    """Map a finished session turn to a row status. The session's last message decides."""
    head = (text or "").lstrip().upper()
    if not ok:
        return "blocked"
    if head.startswith(f"ASK {agent_name.upper()}:"):
        return "needs-agent"
    if head.startswith("NEEDS HUMAN:"):
        return "needs-human"
    return "done"


def for_member(requested_by: str, reason: str, main_names: Iterable[str]) -> str:
    """One front door: every wake-up goes to the chief-of-staff agent. When a team member asked, the reason says
    so ("[for job-lane] ..."), and the chief of staff routes it to that member."""
    who = (requested_by or "").strip().lstrip("@")
    if who and who.lower() not in {n.lower() for n in main_names}:
        return f"[for {who}] {reason}"
    return reason
