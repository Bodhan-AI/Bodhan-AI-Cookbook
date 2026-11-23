from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from bodhan_agent.schemas.ledger import ActionRecord, Artifact, Ledger

DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64


def artifact(**overrides: Any) -> Artifact:
    fields: dict[str, Any] = {"id": "A1", "path": "/run/work/a1.md", "kind": "markdown", "producer": "bodhan"}
    fields.update(overrides)
    if fields["producer"] == "bodhan":
        fields.setdefault("tool", "translate_text")
    return Artifact(**fields)


def user_file(artifact_id: str = "f1", path: str = "/run/inputs/chapter.pdf") -> Artifact:
    return Artifact(id=artifact_id, path=path, kind="pdf", producer="user_input")


def action(seq: int, outcome: str, **overrides: Any) -> ActionRecord:
    fields: dict[str, Any] = {"seq": seq, "tool": "translate_text", "args_digest": DIGEST, "outcome": outcome}
    fields.update(overrides)
    return ActionRecord(**fields)


def test_next_ids_start_at_one() -> None:
    ledger = Ledger()
    assert [ledger.next_id(origin) for origin in ("input", "literal", "produced")] == ["f1", "L1", "A1"]


def test_l0_is_reserved_for_the_original_task() -> None:
    ledger = Ledger()
    ledger.add(Artifact(id="L0", path="/run/work/L0.txt", kind="text", producer="user_input"))
    assert ledger.next_id("literal") == "L1"
    ledger.add(Artifact(id="L1", path="/run/work/L1.txt", kind="text", producer="user_input"))
    assert ledger.next_id("literal") == "L2"


def test_next_id_follows_highest_number() -> None:
    ledger = Ledger()
    ledger.add(user_file("f1", "/run/inputs/a.pdf"))
    ledger.add(user_file("f3", "/run/inputs/b.pdf"))
    assert ledger.next_id("input") == "f4"


@pytest.mark.parametrize("bad_id", ["A0", "f0", "x1", "A", "A1\n", "a1", "L-1"])
def test_bad_artifact_ids_rejected(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        artifact(id=bad_id)


def test_relative_path_rejected() -> None:
    with pytest.raises(ValidationError, match="absolute"):
        artifact(path="work/a1.md")


@pytest.mark.parametrize("bad_sha", ["abc", "A" * 64, "a" * 63, "a" * 64 + "\n"])
def test_bad_sha256_rejected(bad_sha: str) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        artifact(sha256=bad_sha)


@pytest.mark.parametrize(
    ("artifact_id", "producer"),
    [("f1", "bodhan"), ("L1", "llm_english"), ("A1", "user_input")],
)
def test_id_prefix_must_match_producer(artifact_id: str, producer: str) -> None:
    with pytest.raises(ValidationError, match="producer"):
        artifact(id=artifact_id, producer=producer, tool="ocr_document")


def test_bodhan_output_must_name_tool() -> None:
    with pytest.raises(ValidationError, match="name its tool"):
        Artifact(id="A1", path="/run/work/a1.md", kind="markdown", producer="bodhan")


def test_llm_english_cannot_hold_indic_text() -> None:
    with pytest.raises(ValidationError, match="cannot contain Indian-language text"):
        artifact(producer="llm_english", indic_text=True)


@pytest.mark.parametrize(("artifact_id", "producer"), [("f1", "user_input"), ("A1", "llm_english")])
def test_user_and_llm_content_cannot_be_trusted(artifact_id: str, producer: str) -> None:
    with pytest.raises(ValidationError, match="always untrusted"):
        artifact(id=artifact_id, producer=producer, untrusted=False)


def test_new_artifacts_are_pending_and_unusable() -> None:
    item = artifact()
    assert item.step_status == "pending"
    assert item.usable is False


@pytest.mark.parametrize(("status", "usable"), [("ok", True), ("warning", True), ("failed", False), ("pending", False)])
def test_usable_follows_step_status(status: str, usable: bool) -> None:
    ledger = Ledger()
    ledger.add(artifact())
    assert ledger.set_status("A1", status).usable is usable


def test_set_status_appends_warnings() -> None:
    ledger = Ledger()
    ledger.add(artifact(warnings=["first"]))
    ledger.set_status("A1", "warning", ["second"])
    assert ledger.get("A1").warnings == ["first", "second"]


def test_set_status_rejects_unknown_status() -> None:
    ledger = Ledger()
    ledger.add(artifact())
    with pytest.raises(ValidationError):
        ledger.set_status("A1", "done")


def test_untrusted_propagates_through_lineage() -> None:
    ledger = Ledger()
    ledger.add(user_file())
    ocr = ledger.add(artifact(id="A1", tool="ocr_document", sources=["f1"], untrusted=False))
    english = ledger.add(artifact(id="A2", path="/run/work/a2.md", sources=["A1"], untrusted=False))
    assert ocr.untrusted is True
    assert english.untrusted is True


def test_bodhan_output_from_trusted_inline_text_can_be_trusted() -> None:
    ledger = Ledger()
    item = ledger.add(artifact(untrusted=False))
    assert item.untrusted is False


def test_artifacts_are_untrusted_by_default() -> None:
    assert artifact().untrusted is True


def test_duplicate_id_rejected() -> None:
    ledger = Ledger()
    ledger.add(artifact())
    with pytest.raises(ValueError, match="already exists"):
        ledger.add(artifact(path="/run/work/other.md"))


def test_unknown_source_rejected() -> None:
    ledger = Ledger()
    with pytest.raises(ValueError, match="unknown source artifacts f9"):
        ledger.add(artifact(sources=["f9"]))


def test_duplicate_resolved_path_rejected() -> None:
    ledger = Ledger()
    ledger.add(artifact(path="/run/work/a1.md"))
    with pytest.raises(ValueError, match="already registered"):
        ledger.add(artifact(id="A2", path="/run/work/../work/a1.md", producer="code_composed"))


def test_by_path_resolves_paths() -> None:
    ledger = Ledger()
    ledger.add(artifact(path="/run/work/a1.md"))
    found = ledger.by_path("/run/work/./sub/../a1.md")
    assert found is not None and found.id == "A1"
    assert ledger.by_path("/run/work/missing.md") is None


def test_lineage_walks_all_sources() -> None:
    ledger = Ledger()
    ledger.add(user_file("f1", "/run/inputs/a.pdf"))
    ledger.add(user_file("f2", "/run/inputs/b.pdf"))
    ledger.add(artifact(id="A1", path="/run/work/a1.md", tool="ocr_document", sources=["f1"]))
    ledger.add(artifact(id="A2", path="/run/work/a2.md", tool="ocr_document", sources=["f2"]))
    ledger.add(artifact(id="A3", path="/run/work/a3.md", producer="code_composed", sources=["A1", "A2", "f1"]))
    assert sorted(item.id for item in ledger.lineage("A3")) == ["A1", "A2", "A3", "f1", "f2"]


def test_lineage_of_unknown_artifact_raises() -> None:
    with pytest.raises(KeyError, match="A99"):
        Ledger().lineage("A99")


@pytest.mark.parametrize("bad_digest", ["abc", "A" * 64, "a" * 64 + "\n"])
def test_bad_args_digest_rejected(bad_digest: str) -> None:
    with pytest.raises(ValidationError, match="args_digest"):
        action(1, "ok", args_digest=bad_digest)


def test_successful_action_has_no_error_code() -> None:
    with pytest.raises(ValidationError, match="no error_code"):
        action(1, "ok", error_code="timeout")


@pytest.mark.parametrize("outcome", ["error", "denied"])
def test_failed_or_denied_action_needs_error_code(outcome: str) -> None:
    with pytest.raises(ValidationError, match="needs an error_code"):
        action(1, outcome)


def test_record_rejects_unknown_artifacts() -> None:
    with pytest.raises(ValueError, match="unknown artifacts A9"):
        Ledger().record(action(1, "ok", artifacts=["A9"]))


def test_record_accepts_registered_artifacts() -> None:
    ledger = Ledger()
    ledger.add(artifact())
    ledger.record(action(1, "ok", artifacts=["A1"]))
    assert ledger.actions[0].artifacts == ["A1"]


def test_failed_before_none_without_attempts() -> None:
    assert Ledger().failed_before("translate_text", DIGEST) is None


def test_failed_before_returns_non_retryable_failure() -> None:
    ledger = Ledger()
    ledger.record(action(1, "error", error_code="invalid_input", retryable=False))
    found = ledger.failed_before("translate_text", DIGEST)
    assert found is not None and found.seq == 1


def test_failed_before_ignores_other_calls() -> None:
    ledger = Ledger()
    ledger.record(action(1, "error", error_code="invalid_input", retryable=False))
    assert ledger.failed_before("translate_text", OTHER_DIGEST) is None
    assert ledger.failed_before("ocr_document", DIGEST) is None


def test_denied_records_do_not_clear_a_failure() -> None:
    ledger = Ledger()
    ledger.record(action(1, "error", error_code="invalid_input", retryable=False))
    ledger.record(action(2, "denied", error_code="repeat_of_failed_call"))
    ledger.record(action(3, "denied", error_code="repeat_of_failed_call"))
    found = ledger.failed_before("translate_text", DIGEST)
    assert found is not None and found.seq == 1


def test_later_success_clears_failure() -> None:
    ledger = Ledger()
    ledger.record(action(1, "error", error_code="invalid_input", retryable=False))
    ledger.record(action(2, "ok"))
    assert ledger.failed_before("translate_text", DIGEST) is None


def test_later_retryable_error_is_authoritative() -> None:
    ledger = Ledger()
    ledger.record(action(1, "error", error_code="invalid_input", retryable=False))
    ledger.record(action(2, "error", error_code="rate_limited", retryable=True))
    assert ledger.failed_before("translate_text", DIGEST) is None


def test_failure_after_success_blocks_again() -> None:
    ledger = Ledger()
    ledger.record(action(1, "ok"))
    ledger.record(action(2, "error", error_code="invalid_input", retryable=False))
    found = ledger.failed_before("translate_text", DIGEST)
    assert found is not None and found.seq == 2


def test_uncertain_attempts_counted_per_call() -> None:
    ledger = Ledger()
    ledger.record(action(1, "error", error_code="timeout", retryable=True, uncertain=True))
    ledger.record(action(2, "error", error_code="timeout", retryable=True, uncertain=True))
    ledger.record(action(3, "error", error_code="timeout", retryable=True, uncertain=True, args_digest=OTHER_DIGEST))
    ledger.record(action(4, "error", error_code="rate_limited", retryable=True))
    assert ledger.uncertain_attempts("translate_text", DIGEST) == 2
    assert ledger.uncertain_attempts("translate_text", OTHER_DIGEST) == 1
    assert ledger.uncertain_attempts("ocr_document", DIGEST) == 0
