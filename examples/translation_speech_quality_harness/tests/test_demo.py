import subprocess
import sys
from pathlib import Path


def test_build_demo_dry_run():
    recipe = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/build_demo.py", "--dry-run"],
        cwd=recipe,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Estimated" in result.stdout


def test_build_demo_default_output_is_recipe_data():
    recipe = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(recipe / "scripts" / "build_demo.py"), "--dry-run"],
        cwd=recipe.parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
