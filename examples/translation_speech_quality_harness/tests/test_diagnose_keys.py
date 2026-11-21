import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_keys.py"
SPEC = importlib.util.spec_from_file_location("diagnose_keys", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
diagnose_keys = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnose_keys)


def test_key_diagnostics_never_include_values(monkeypatch):
    secret = "sensitive-value-not-for-output"
    for name in diagnose_keys.KEY_NAMES:
        monkeypatch.setenv(name, secret)
    report = diagnose_keys.inspect_keys()

    rendered = repr(report)
    assert secret not in rendered
    assert all(details["present"] is True for name, details in report.items() if name != "_identical_values")
    assert report["_identical_values"]["pairs"] == [
        sorted([
            "BODHAN_SPEAK_KEY",
            "BODHAN_TRANSLATE_KEY",
            "BODHAN_TRANSCRIBE_KEY",
            "LLM_API_KEY",
        ])
    ]
