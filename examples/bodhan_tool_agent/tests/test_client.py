import pytest

from bodhan_mcp.client import BodhanClient, get_client
from bodhan_mcp.errors import BudgetExceeded, ToolError


def test_retries_on_429_then_succeeds(fake):
    fake.fail_next = [429, 503]
    r = get_client().translate("Hello world.", "ta")
    assert r["translation"] and not r["cached"]


def test_auth_error_is_not_retried(fake):
    fake.fail_next = [401, 401, 401]
    with pytest.raises(ToolError) as e:
        get_client().translate("Hello.", "ta")
    assert e.value.code == "auth_error" and len(fake.fail_next) == 2


def test_identical_request_is_served_from_cache_and_not_billed(fake):
    c = get_client()
    c.translate("Water boils at 100 degrees.", "hi")
    spent = c.ledger.spent
    r = c.translate("Water boils at 100 degrees.", "hi")
    assert r["cached"] and fake.count("mt") == 1 and c.ledger.spent == spent


def test_budget_checked_before_sending(fake, tmp_path):
    import httpx
    c = BodhanClient(cache_dir=tmp_path / "c2", cap_inr=0.01, transport=httpx.MockTransport(fake))
    with pytest.raises(BudgetExceeded):
        c.speak("x" * 2000, "Kavya", "hi")  # ~Rs 1.2
    assert fake.count("tts") == 0


def test_missing_key_gives_actionable_error(fake, monkeypatch):
    monkeypatch.delenv("BODHAN_SPEAK_KEY")
    with pytest.raises(ToolError) as e:
        get_client().speak("hi", "Kavya", "hi")
    assert e.value.code == "missing_api_key" and "BODHAN_SPEAK_KEY" in e.value.hint or "BODHAN_SPEAK_KEY" in e.value.message


def test_single_shared_key_is_accepted_as_a_fallback(fake, monkeypatch):
    for env in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY"):
        monkeypatch.delenv(env)
    monkeypatch.setenv("BODHAN_API_KEY", "shared")
    assert get_client().translate("Hello world.", "ta")["translation"]
