import json
from pathlib import Path

import pytest
from bodhan_checks import check_api, check_security, check_structure, has_errors, run_all

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "examples" / "TEMPLATE"
FAKE_SK = "sk-" + "abcdefghij" * 3  # assembled so the scanner never matches this file itself
FAKE_HEX = "0123456789" + "abcdef" * 3


def _notebook(cells):
    return json.dumps({
        "cells": cells,
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    })


def _code_cell(src, outputs=None, count=None):
    return {"cell_type": "code", "source": src, "metadata": {}, "outputs": outputs or [], "execution_count": count}


@pytest.fixture
def recipe(tmp_path):
    """A minimal recipe that passes every check."""
    root = tmp_path / "good_recipe"
    root.mkdir()
    (root / "README.md").write_text("# Good\n\n## Setup\n\npip\n\n## Run\n\ngo\n")
    (root / "requirements.txt").write_text("requests==2.32.3\n")
    (root / ".env.example").write_text("BODHAN_API_KEY=<your key>\n")
    (root / ".gitignore").write_text(".env\n")
    (root / "good_recipe.ipynb").write_text(_notebook([_code_cell('model = "indic-translate"')]))
    return root


def test_good_recipe_has_no_findings(recipe):
    assert run_all(recipe) == []


def test_template_passes_structure_and_api_checks():
    # The template README deliberately keeps placeholders; only the placeholder rule may fire.
    findings = [f for f in run_all(TEMPLATE) if "placeholder" not in f.message]
    assert findings == [], [str(f) for f in findings]


# --- security ---------------------------------------------------------------

def test_committed_env_file_is_an_error(recipe):
    (recipe / ".env").write_text(f"BODHAN_API_KEY={FAKE_SK}\n")
    findings = list(check_security(recipe))
    assert any(f.level == "error" and ".env" in f.path and "environment file" in f.message for f in findings)


def test_sk_key_in_notebook_is_an_error(recipe):
    (recipe / "good_recipe.ipynb").write_text(_notebook([_code_cell(f'key = "{FAKE_SK}"')]))
    assert has_errors(check_security(recipe))


def test_literal_bodhan_key_assignment_is_an_error(recipe):
    (recipe / "app.py").write_text(f'BODHAN_API_KEY = "{FAKE_HEX}"\n')
    assert has_errors(check_security(recipe))


def test_placeholder_key_assignment_is_fine(recipe):
    (recipe / "app.py").write_text('BODHAN_API_KEY = "<your-key-here>"\n')
    assert list(check_security(recipe)) == []


def test_env_example_placeholder_is_fine(recipe):
    assert list(check_security(recipe)) == []


# --- structure --------------------------------------------------------------

def test_missing_readme_is_an_error(recipe):
    (recipe / "README.md").unlink()
    assert any("README.md is missing" in f.message for f in check_structure(recipe))


def test_unpinned_requirement_is_an_error(recipe):
    (recipe / "requirements.txt").write_text("requests\n")
    assert any("unpinned" in f.message and f.level == "error" for f in check_structure(recipe))


def test_missing_env_example_is_an_error(recipe):
    (recipe / ".env.example").unlink()
    assert any(".env.example is missing" in f.message for f in check_structure(recipe))


def test_env_example_check_can_be_skipped(recipe):
    (recipe / ".env.example").unlink()
    (recipe / ".gitignore").unlink()
    assert not has_errors(check_structure(recipe, require_env_example=False))


def test_gitignore_must_ignore_env(recipe):
    (recipe / ".gitignore").write_text("__pycache__/\n")
    assert any("does not ignore .env" in f.message for f in check_structure(recipe))


def test_notebook_outputs_are_an_error(recipe):
    nb = _notebook([_code_cell("print(1)", outputs=[{"output_type": "stream", "name": "stdout", "text": "1\n"}], count=1)])
    (recipe / "good_recipe.ipynb").write_text(nb)
    assert any("outputs" in f.message and f.level == "error" for f in check_structure(recipe))


def test_invalid_notebook_json_is_an_error(recipe):
    (recipe / "good_recipe.ipynb").write_text("{not json")
    assert any("not valid JSON" in f.message for f in check_structure(recipe))


def test_camel_case_notebook_is_a_warning(recipe):
    (recipe / "good_recipe.ipynb").rename(recipe / "GoodRecipe.ipynb")
    findings = list(check_structure(recipe))
    assert any("snake_case" in f.message and f.level == "warning" for f in findings)
    assert not has_errors(findings)


def test_js_recipe_needs_package_json(tmp_path):
    root = tmp_path / "js_recipe"
    root.mkdir()
    (root / "README.md").write_text("# JS\n\n## Setup\n\n## Run\n")
    (root / ".env.example").write_text("BODHAN_API_KEY=<key>\n")
    (root / ".gitignore").write_text(".env\n")
    (root / "index.ts").write_text('const model = "indic-speak";\n')
    assert any("package.json" in f.message for f in check_structure(root))


# --- api --------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    'model = "bodhan-mt"',
    '{"model": "bodhan-asr"}',
    "-F model=bodhan-ocr",
])
def test_deprecated_model_ids_are_errors(recipe, src):
    (recipe / "app.py").write_text(src + "\n")
    findings = list(check_api(recipe))
    assert any(f.level == "error" and "deprecated" in f.message for f in findings), findings


def test_unknown_indic_model_is_an_error(recipe):
    (recipe / "app.py").write_text('model = "indic-imaginary"\n')
    assert has_errors(check_api(recipe))


def test_third_party_model_is_allowed_silently(recipe):
    (recipe / "app.py").write_text('llm = client.chat.completions.create(model="gpt-4o-mini", messages=[])\n')
    assert list(check_api(recipe)) == []


def test_bcp47_in_suffix_is_a_warning(recipe):
    (recipe / "app.py").write_text('language = "hi-IN"\n')
    findings = list(check_api(recipe))
    assert any(f.level == "warning" and "hi-IN" in f.message for f in findings)
    assert not has_errors(findings)


def test_all_shipped_recipes_pass():
    """Every recipe in the repo must pass its own gate."""
    from ci_validate import NO_ENV_EXAMPLE, all_recipe_dirs

    for root in all_recipe_dirs():
        if root.name == "TEMPLATE":
            continue
        require_env = root.parent.name not in NO_ENV_EXAMPLE
        findings = run_all(root, require_env_example=require_env)
        assert not has_errors(findings), [str(f) for f in findings]
