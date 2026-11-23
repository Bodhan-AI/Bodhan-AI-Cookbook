import json
from pathlib import Path

from scripts.validate_recipe import validate_recipe


def _recipe(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(
        "# Harness\n\n## Setup\n\nInstall dependencies.\n\n## Run\n\nRun the CLI.\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("httpx==0.28.1\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text(
        "# BODHAN_API_KEY: per-model keys are used instead\nBODHAN_TRANSLATE_KEY=test-key\n",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text(
        ".env\n.cache/\ncache/\nreports/\noutputs/\n*.wav\n",
        encoding="utf-8",
    )
    package = tmp_path / "src" / "bodhan_eval"
    package.mkdir(parents=True)
    (package / "client.py").write_text(
        'BASE_URL = "https://api.bodhan.ai"\nMODEL = "indic-translate"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_valid_recipe_passes(tmp_path: Path) -> None:
    assert validate_recipe(_recipe(tmp_path)) == []


def test_recipe_validator_reports_contract_violations(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path)
    (recipe / "requirements.txt").write_text("httpx>=0.28\n", encoding="utf-8")
    (recipe / ".env.example").write_text("BODHAN_API_KEY=test-key\n", encoding="utf-8")
    (recipe / ".gitignore").write_text(".env\n", encoding="utf-8")
    (recipe / "README.md").write_text("# Harness\n\n## Setup\nTODO\n", encoding="utf-8")

    problems = validate_recipe(recipe)

    assert any("requirements.txt" in problem and "pin every" in problem for problem in problems)
    assert any(".env.example" in problem and "per-model" in problem for problem in problems)
    assert any(".gitignore" in problem and "generated audio" in problem for problem in problems)
    assert any("README.md" in problem and "## Run" in problem for problem in problems)
    assert any("placeholder" in problem for problem in problems)


def test_recipe_validator_rejects_env_files_and_unsafe_source_literals(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path)
    (recipe / ".env").write_text("BODHAN_TRANSLATE_KEY=test-key\n", encoding="utf-8")
    source = recipe / "src" / "bodhan_eval" / "client.py"
    invalid_model = "indic-" + "made-up"
    secret_shaped = "sk-" + ("A" * 20)
    source.write_text(
        f'MODEL = "{invalid_model}"\n'
        f"TOKEN = {json.dumps(secret_shaped)}\n"
        'JUDGE_URL = "https://example.invalid/v1"\n',
        encoding="utf-8",
    )

    problems = validate_recipe(recipe)

    assert any(".env file" in problem for problem in problems)
    assert any("model id" in problem for problem in problems)
    assert any("secret-shaped" in problem for problem in problems)
    assert any("non-Bodhan service URL" in problem for problem in problems)
