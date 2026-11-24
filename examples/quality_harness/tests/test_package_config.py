from pathlib import Path


def test_packaged_defaults_match_recipe_config():
    root = Path(__file__).resolve().parent.parent
    for name in ("languages.yaml", "bodhan_api.yaml", "thresholds.json"):
        assert (root / "config" / name).read_bytes() == (
            root / "src" / "bodhan_eval" / "default_config" / name).read_bytes()
