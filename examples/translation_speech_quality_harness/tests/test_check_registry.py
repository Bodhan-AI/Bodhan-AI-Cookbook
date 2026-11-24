from bodhan_eval.budget import Budget
from bodhan_eval.checks import CHECK_REGISTRY, register_check
from bodhan_eval.dataset import DatasetResult
from bodhan_eval.models import Flag, Item
from bodhan_eval.pipeline import run_pipeline


def test_test_defined_check_runs_without_pipeline_edit():
    name = "test_registry_toy"

    @register_check(
        name=name,
        categories=["mistranslation"],
        modality="text",
        cost_tier=0,
    )
    def toy_check(item, stats):
        del stats
        if "sentinel" in (item.translation or ""):
            return [Flag(
                category="mistranslation",
                severity="minor",
                modality="text",
                span=(0, 8),
                detectors=[name],
                confidence=1.0,
            )]
        return []

    try:
        records = run_pipeline(
            DatasetResult(
                items=[
                    Item(
                        id="toy",
                        lang="hi",
                        source="Hello",
                        translation="sentinel अनुवाद",
                    )
                ],
                total_input_lines=1,
            ),
            Budget(1.0),
        )
        record = records[0]
        assert name in record["checks_run"]
        assert any(flag["category"] == "mistranslation" for flag in record["flags"])
        assert any(decision["check"] == name for decision in record["decisions"])
    finally:
        CHECK_REGISTRY.pop(name, None)


def test_registry_sorts_by_cost_tier():
    from bodhan_eval.checks import registered_checks

    specs = registered_checks("text")
    assert [spec.cost_tier for spec in specs] == sorted(spec.cost_tier for spec in specs)
