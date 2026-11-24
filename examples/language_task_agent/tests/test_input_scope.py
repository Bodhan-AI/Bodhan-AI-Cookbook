from pathlib import Path

import pytest

from bodhan_agent.checkpoint import inputs_fingerprint
from conftest import input_ref


@pytest.mark.parametrize("consumer", ["manifest", "fingerprint"])
@pytest.mark.parametrize("empty", [False, True])
async def test_explicit_inputs_never_scan_the_common_parent(
    tools, run, monkeypatch, consumer, empty
):
    source = input_ref(run)
    unrelated = run.input_root / "unrelated.txt"
    unrelated.write_text("Not selected by the caller.")
    run.input_files = [] if empty else [run.resolve(source)]

    def unexpected_scan(self, pattern):
        pytest.fail("Explicit inputs must not enumerate the common parent directory.")

    monkeypatch.setattr(Path, "rglob", unexpected_scan)
    if consumer == "manifest":
        result = await tools.call("list_files", {})
        assert result["ok"]
        assert [item["ref"] for item in result["files"]] == ([] if empty else [source])
    else:
        result = inputs_fingerprint(run)
        assert [item[0] for item in result] == ([] if empty else [str(run.resolve(source))])
