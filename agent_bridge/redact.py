"""Secret redaction for everything the PC uploads about itself (the pulse file).

The rule is to over-redact: a long path or hash that gets masked costs nothing, a leaked key costs a lot.
Always redact BEFORE truncating text, otherwise a key cut in half can slip under the minimum lengths below."""
from __future__ import annotations

import re

_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{8,}",                                   # OpenAI / Anthropic style keys
    r"gh[pousr]_[A-Za-z0-9]{10,}",                             # GitHub tokens
    r"github_pat_[A-Za-z0-9_]{20,}",                           # GitHub fine-grained tokens
    r"xox[abpr]-[A-Za-z0-9-]{8,}",                             # Slack tokens
    r"AIza[0-9A-Za-z_-]{20,}",                                 # Google API keys
    r"ya29\.[0-9A-Za-z_-]{20,}",                               # Google OAuth access tokens
    r"eyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+)*",             # JWTs
    r"(?i:bearer)\s+[A-Za-z0-9._~+/=-]{12,}",                  # Authorization headers
    r"(?i:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+",   # key=value / key: value
    r"[A-Fa-f0-9]{32,}",                                       # long hex (webhook secrets, hashes)
    r"[A-Za-z0-9+/_-]{40,}",                                   # any other long opaque string
]
SECRET = re.compile("|".join(f"(?:{p})" for p in _PATTERNS))

# File names that are never listed, not even by name.
SKIP_NAME = re.compile(r"(^\.env|secret|credential|token|password|\.key$|\.pem$|\.p12$|id_rsa|cookies)", re.I)


def redact(text: str) -> str:
    return SECRET.sub("[redacted]", text or "")
