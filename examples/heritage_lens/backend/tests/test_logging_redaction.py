import logging

from app.core import logging as hl_logging


def test_known_secret_and_patterns_are_redacted():
    hl_logging.clear_secrets()
    hl_logging.register_secret("fake-test-secret-AAAA-BBBB-123456")
    out = hl_logging.redact("key=fake-test-secret-AAAA-BBBB-123456 ok")
    assert "fake-test-secret" not in out
    assert "abc.def" not in hl_logging.redact("Authorization: Bearer abc.def-123")
    assert "hunter2" not in hl_logging.redact('{"password": "hunter2"}')
    assert "xyz" not in hl_logging.redact("api_key=xyz")
    assert hl_logging.redact("nothing secret here") == "nothing secret here"


def test_filter_rewrites_log_records():
    hl_logging.clear_secrets()
    hl_logging.register_secret("super-secret-value")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "sent %s", ("super-secret-value",), None)
    hl_logging.RedactingFilter().filter(rec)
    assert "super-secret-value" not in rec.getMessage()
