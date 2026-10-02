"""The shared folder both sides can see: the PC's pulse file, full results, the agent's own status file and
task drops. Local backend: a plain directory. Google Drive: `google_backends.DriveFolderStore`."""
from __future__ import annotations

from pathlib import Path


class Store:
    def read(self, name: str) -> tuple[str, str] | None:
        """(text, change marker) or None when the file does not exist."""
        raise NotImplementedError

    def modified(self, name: str) -> str | None:
        """A marker that changes whenever the file changes (no download needed). None if missing."""
        raise NotImplementedError

    def write(self, name: str, text: str) -> None:
        raise NotImplementedError

    def list(self, prefix: str) -> list[str]:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


class LocalFolderStore(Store):
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _p(self, name: str) -> Path:
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"bad file name {name!r}")
        return self.folder / name

    def read(self, name: str) -> tuple[str, str] | None:
        p = self._p(name)
        if not p.is_file():
            return None
        return p.read_text("utf-8", errors="replace"), str(p.stat().st_mtime_ns)

    def modified(self, name: str) -> str | None:
        p = self._p(name)
        return str(p.stat().st_mtime_ns) if p.is_file() else None

    def write(self, name: str, text: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self._p(name)
        tmp = p.with_name("." + p.name + ".tmp")
        tmp.write_text(text, "utf-8")
        tmp.replace(p)

    def list(self, prefix: str) -> list[str]:
        if not self.folder.is_dir():
            return []
        return sorted(p.name for p in self.folder.iterdir() if p.is_file() and p.name.startswith(prefix))

    def delete(self, name: str) -> None:
        self._p(name).unlink(missing_ok=True)
