import ast
from pathlib import Path

from bodhan_eval.config import CHECK_DEFAULTS, CHECK_THRESHOLDS

RECIPE = Path(__file__).resolve().parents[1]


def test_documented_threshold_defaults():
    assert CHECK_THRESHOLDS["speech_silence_ratio"]["value"] == 0.8
    assert CHECK_THRESHOLDS["clipping_peak"]["value"] == 0.999
    assert CHECK_THRESHOLDS["text_length_mad_multiplier"]["value"] == 3.0
    assert all(value["origin"] for value in CHECK_THRESHOLDS.values())
    assert CHECK_DEFAULTS["confidence"]["text_numbers"] == 0.95


def test_checks_have_no_undocumented_float_literals():
    allowed = {0.0, 0.5, 1.0}
    checks_dir = RECIPE / "src" / "bodhan_eval" / "checks"
    violations = []
    for path in checks_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if node.value not in allowed:
                    violations.append(f"{path.name}:{node.lineno}={node.value}")
    assert not violations, "\n".join(violations)
