import pytest

from bodhan_agent import llm
from bodhan_mcp.core.errors import BodhanError


@pytest.mark.parametrize("text", ["नमस्ते", "தமிழ்", "తెలుగు", "বাংলা", "اردو", "سنڌي", "ᱥᱟᱱᱛᱟᱲᱤ"])
def test_the_real_llm_client_refuses_indic_text_before_any_request(text, monkeypatch):
    monkeypatch.setattr(llm, "_client", lambda: pytest.fail("a request would have been made"))
    with pytest.raises(BodhanError):
        llm.chat([{"role": "user", "content": f"summarise {text}"}])
