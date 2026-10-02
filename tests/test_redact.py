"""Redaction: anything key-shaped never reaches the pulse file. Test secrets are built at runtime on purpose,
so no secret-shaped string sits in the repository."""
from agent_bridge.redact import SKIP_NAME, redact
from agent_bridge.util import one_line

FAKES = {
    "openai/anthropic style": "sk-" + "A1b2" * 6,
    "github token": "ghp_" + "x9Y8" * 9,
    "slack token": "xoxb-" + "1234-" * 3 + "abcdEFGH",
    "google api key": "AIza" + "B7c" * 11,
    "jwt": "eyJ" + "hbGciOiJIUzI1" * 2 + "." + "e30",
    "long hex": "deadbeef" * 5,
    "long opaque": "Zm9v" * 12,
}


def test_known_key_shapes_are_redacted():
    for label, secret in FAKES.items():
        out = redact(f"use {secret} for staging")
        assert secret not in out, label
        assert "[redacted]" in out, label


def test_bearer_and_key_value_pairs():
    assert "letmein" not in redact("password: letmein-letmein")
    assert "abc123" not in redact("api_key=abc123")
    assert "q8" not in redact("Authorization: Bearer " + "q8Zt" * 5)


def test_normal_text_is_untouched():
    text = "Draft a short project update for the team about the onboarding flow (Monday 05.10)"
    assert redact(text) == text


def test_redact_before_truncate_never_leaks_half_a_key():
    secret = "sk-" + "Q7w" * 10
    prompt = "x" * 200 + " " + secret
    safe = one_line(redact(prompt), 220)
    assert "sk-" not in safe and "Q7wQ7w" not in safe
    # the wrong order would cut the key below the pattern's minimum length and leak a piece of it
    unsafe = redact(one_line(prompt, 214))
    assert "sk-Q7wQ7w" in unsafe


def test_secret_looking_file_names_are_never_listed():
    for name in (".env", ".env.local", "credentials.json", "token.json", "server.pem", "id_rsa", "api-token.txt",
                 "cookies.sqlite", "my-secret-notes.md", "passwords.csv"):
        assert SKIP_NAME.search(name), name
    for name in ("notes.md", "ledger.csv", "plan.txt", "README.md"):
        assert not SKIP_NAME.search(name), name
