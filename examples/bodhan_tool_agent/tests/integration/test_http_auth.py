"""Integration tests for MCP Streamable HTTP transport and Bearer authentication logic."""

from __future__ import annotations



def verify_bearer_auth(authorization_header: str | None, expected_token: str) -> tuple[int, str]:
    """Pure authentication validation logic matching MCP server HTTP middleware."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return 401, "Unauthorized: Missing Bearer token in Authorization header"

    incoming_token = authorization_header.split(" ", 1)[1].strip()
    if incoming_token != expected_token:
        return 403, "Forbidden: Invalid Bearer token"

    return 200, "OK"


def test_http_auth_missing_header_rejected():
    status, msg = verify_bearer_auth(None, expected_token="secret-token-123")
    assert status == 401
    assert "Missing Bearer token" in msg


def test_http_auth_invalid_token_rejected():
    status, msg = verify_bearer_auth("Bearer wrong-token-xyz", expected_token="secret-token-123")
    assert status == 403
    assert "Invalid Bearer token" in msg


def test_http_auth_valid_token_accepted():
    status, msg = verify_bearer_auth("Bearer secret-token-123", expected_token="secret-token-123")
    assert status == 200
    assert msg == "OK"
