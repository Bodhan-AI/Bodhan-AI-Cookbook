import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_new_recipe_scaffolds_from_template(tmp_path, monkeypatch):
    import new_recipe

    fake_repo = tmp_path / "repo"
    (fake_repo / "examples").mkdir(parents=True)
    src = REPO / "examples" / "TEMPLATE"
    subprocess.run(["cp", "-R", str(src), str(fake_repo / "examples" / "TEMPLATE")], check=True)
    monkeypatch.setattr(new_recipe, "REPO", fake_repo)
    monkeypatch.setattr(new_recipe, "TEMPLATE", fake_repo / "examples" / "TEMPLATE")

    assert new_recipe.main(["voice_notes"]) == 0
    dest = fake_repo / "examples" / "voice_notes"
    assert (dest / "voice_notes.ipynb").exists()
    assert not (dest / "TEMPLATE.ipynb").exists()
    assert "voice_notes" in (dest / "README.md").read_text()
    json.loads((dest / "voice_notes.ipynb").read_text())  # still valid JSON


def test_new_recipe_rejects_bad_names(capsys):
    import new_recipe

    assert new_recipe.main(["Not-Snake"]) == 1
    assert new_recipe.main([]) == 2


def test_clear_outputs_strips_cells(tmp_path):
    import clear_notebook_outputs as cno

    nb = tmp_path / "x.ipynb"
    cell = {"cell_type": "code", "source": "1", "metadata": {}, "outputs": [{"a": 1}], "execution_count": 3}
    nb.write_text(json.dumps({"cells": [cell], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}))
    assert cno.clear(nb) is True
    cell = json.loads(nb.read_text())["cells"][0]
    assert cell["outputs"] == [] and cell["execution_count"] is None
    assert cno.clear(nb) is False


def test_scripts_are_importable_as_modules():
    for name in ("bodhan_rules", "bodhan_checks", "validate_recipe", "ci_validate", "new_recipe", "clear_notebook_outputs"):
        assert name in sys.modules or __import__(name)
