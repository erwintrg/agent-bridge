"""Small shared helpers: atomic JSON state files and a cross-process lock."""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def one_line(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: max(n - 3, 0)] + "..."


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data: Any) -> None:
    """Write to a temp file and rename, so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(path)


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive lock shared by the watcher and the turn processes (both write the board and the state files).
    Not re-entrant: never nest two locks on the same file. No-op where fcntl does not exist (Windows)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:  # pragma: no cover
        fcntl = None  # type: ignore[assignment]
    if fcntl is None:  # pragma: no cover
        yield
        return
    with open(path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
