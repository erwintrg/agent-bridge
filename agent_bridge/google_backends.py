"""Google Sheets board and Google Drive folder, over the plain REST APIs (standard library only).

Credentials: an OAuth client plus a refresh token with the Sheets and Drive scopes, from GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN. They are read when a request is made and never logged."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Mapping

from .board import Board, BoardError
from .clock import Clock
from .rows import COLUMNS
from .store import Store

TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class GoogleApi:
    def __init__(self, env: Mapping[str, str]):
        self._env = env
        self._token, self._expires = "", 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        missing = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN") if not self._env.get(k)]
        if missing:
            raise BoardError(f"missing settings: {', '.join(missing)}")
        data = urllib.parse.urlencode({"client_id": self._env["GOOGLE_CLIENT_ID"],
                                       "client_secret": self._env["GOOGLE_CLIENT_SECRET"],
                                       "refresh_token": self._env["GOOGLE_REFRESH_TOKEN"],
                                       "grant_type": "refresh_token"}).encode()
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=30) as r:
            j = json.load(r)
        self._token, self._expires = j["access_token"], time.time() + int(j.get("expires_in", 3000))
        return self._token

    def request(self, method: str, url: str, body=None, *, raw: bytes | None = None, content_type: str = "",
                timeout: int = 60) -> bytes:
        headers = {"Authorization": "Bearer " + self.token()}
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif raw is not None:
            data, headers["Content-Type"] = raw, content_type
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def json(self, method: str, url: str, body=None, **kw):
        out = self.request(method, url, body, **kw)
        return json.loads(out) if out.strip() else {}


def col_letter(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


class SheetsBoard(Board):
    """One tab ("Tasks") with the header row id | created | requested_by | owner | title | detail | status |
    result | updated. Columns are found by header name, so their order in the sheet does not matter.
    Limitation: the next id is read-then-append (Sheets has no transaction); the same-id guard catches a clash."""

    def __init__(self, api: GoogleApi, sheet_id: str, tab: str, clock: Clock):
        super().__init__(clock)
        if not sheet_id:
            raise BoardError("SHEET_ID is not set")
        self.api, self.sid, self.tab = api, sheet_id, tab
        self._header: list[str] = []

    def _url(self, rng: str, suffix: str = "") -> str:
        return f"{SHEETS}/{self.sid}/values/{urllib.parse.quote(rng)}{suffix}"

    def _read(self) -> list[dict]:
        vals = self.api.json("GET", self._url(self.tab)).get("values", [])
        if not vals:
            raise BoardError(f"tab {self.tab} has no header row")
        self._header = [h.strip() for h in vals[0]]
        missing = [c for c in COLUMNS if c not in self._header]
        if missing:
            raise BoardError(f"tab {self.tab} lacks columns {missing}")
        n = len(self._header)
        return [dict(zip(self._header, list(r) + [""] * (n - len(r)))) for r in vals[1:]]

    def _append(self, values: dict) -> None:
        if not self._header:
            self._read()
        row = [values.get(h, "") for h in self._header]
        self.api.json("POST", self._url(f"{self.tab}!A1", ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"),
                      {"values": [row]})

    def _write(self, rownum: int, changes: dict) -> None:
        if not self._header:
            self._read()
        data = [{"range": f"{self.tab}!{col_letter(self._header.index(k))}{rownum}", "values": [[v]]}
                for k, v in changes.items()]
        self.api.json("POST", f"{SHEETS}/{self.sid}/values:batchUpdate", {"valueInputOption": "RAW", "data": data})


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


class DriveFolderStore(Store):
    def __init__(self, api: GoogleApi, folder_id: str):
        if not folder_id:
            raise BoardError("DRIVE_FOLDER_ID is not set")
        self.api, self.folder = api, folder_id

    def _find(self, name: str):
        q = f"name='{_q(name)}' and '{_q(self.folder)}' in parents and trashed=false"
        url = f"{DRIVE}?q={urllib.parse.quote(q)}&fields=files(id,modifiedTime)&orderBy=modifiedTime%20desc"
        files = self.api.json("GET", url).get("files", [])
        return files[0] if files else None

    def read(self, name: str):
        f = self._find(name)
        if not f:
            return None
        body = self.api.request("GET", f"{DRIVE}/{f['id']}?alt=media").decode("utf-8", "replace")
        return body, f["modifiedTime"]

    def modified(self, name: str):
        f = self._find(name)
        return f["modifiedTime"] if f else None

    def write(self, name: str, text: str) -> None:
        f = self._find(name)
        if f:
            self.api.request("PATCH", f"{UPLOAD}/{f['id']}?uploadType=media", raw=text.encode(),
                             content_type="text/markdown; charset=UTF-8")
            return
        b = "agentbridgeboundary"
        meta = json.dumps({"name": name, "parents": [self.folder]})
        data = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{b}\r\n"
                f"Content-Type: text/markdown; charset=UTF-8\r\n\r\n{text}\r\n--{b}--").encode()
        self.api.request("POST", f"{UPLOAD}?uploadType=multipart", raw=data,
                         content_type=f"multipart/related; boundary={b}")

    def list(self, prefix: str) -> list[str]:
        q = f"name contains '{_q(prefix)}' and '{_q(self.folder)}' in parents and trashed=false"
        files = self.api.json("GET", f"{DRIVE}?q={urllib.parse.quote(q)}&fields=files(name)&pageSize=100").get("files", [])
        return sorted(f["name"] for f in files if f["name"].startswith(prefix))

    def delete(self, name: str) -> None:
        f = self._find(name)
        if f:  # to the bin, not gone: a person can still restore a drop that was ingested by mistake
            self.api.json("PATCH", f"{DRIVE}/{f['id']}", {"trashed": True})
