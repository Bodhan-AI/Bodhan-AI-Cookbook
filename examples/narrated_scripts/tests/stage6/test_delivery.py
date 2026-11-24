"""Offline checks for the reviewer entry points and Cookbook packaging docs."""

import re
import shlex
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[2]


def headings(markdown):
    return set(re.findall(r"^## (.+)$", markdown, flags=re.MULTILINE))


def test_readme_exposes_full_evaluator_command():
    readme = (RECIPE / "README.md").read_text(encoding="utf-8")
    assert {"Setup", "Run", "How it works", "Results", "Demo video", "Offline tests",
            "Trade-offs and known limitations", "Cost and runtime", "AI assistance"} <= headings(readme)
    assert "make setup" in readme

    [invocation] = re.findall(r"^BODHAN_BACKEND=hosted python -m bodhan_lesson .+$", readme, re.MULTILINE)
    args = shlex.split(invocation)
    for flag, value in (("--input", "$INPUT"), ("--langs", "hi,ta,bn"), ("--out", "$OUT")):
        assert args[args.index(flag) + 1] == value
    assert "--until" not in args


def test_reviewer_links_and_assignment_pr_sections():
    readme = (RECIPE / "README.md").read_text(encoding="utf-8")
    expected = {
        "docs/reviewer/design.md#architecture": "Architecture",
        "docs/reviewer/design.md#workflow": "Workflow",
        "docs/reviewer/design.md#design-notes": "Design notes",
        "docs/reviewer/evidence.md#demo-1-ncert-lines-and-angles": "Demo 1 NCERT Lines and Angles",
        "docs/reviewer/evidence.md#demo-2-indian-language-source": "Demo 2 Indian-language source",
        "docs/reviewer/evidence.md#hosted-benchmark": "Hosted benchmark",
        "docs/reviewer/evidence.md#demo-video": "Demo video",
    }
    for target, heading in expected.items():
        assert f"]({target})" in readme
        document = (RECIPE / target.partition("#")[0]).read_text(encoding="utf-8")
        assert heading in headings(document)

    design = (RECIPE / "docs/reviewer/design.md").read_text(encoding="utf-8")
    assert design.count("```mermaid\n") == 2
    submission = (RECIPE / "docs/reviewer/submission.md").read_text(encoding="utf-8")
    assert {"Summary", "How to run", "Architecture", "Workflow", "Where the LLM is used, and why",
            "How tables, equations and figures are narrated", "Bodhan models used", "Results", "Demo video",
            "Trade-offs and known limitations", "Cost and runtime", "AI assistance"} <= headings(submission)
    assert "make check" in submission
    assert "git rev-parse HEAD" in submission


def test_cookbook_layout_keeps_runtime_dependencies_pinned():
    requirements = (RECIPE / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert requirements
    assert all(re.fullmatch(r'[\w.-]+==[\w.]+(?:; python_version < "[\d.]+")?', requirement)
               for requirement in requirements)
    source_pin = (RECIPE / "requirements-source.txt").read_text(encoding="utf-8").splitlines()[-1]
    assert re.fullmatch(r"[\w.-]+ @ https://\S+/[a-f0-9]{40}#sha256=[a-f0-9]{64}", source_pin)
    package_config = (RECIPE / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dependencies = {file = ["requirements.txt", "requirements-source.txt"]}' in package_config

    env = (RECIPE / ".env.example").read_text(encoding="utf-8").splitlines()
    declared = dict(line.split("=", 1) for line in env if line and not line.startswith("#"))
    assert {"BODHAN_API_KEY", "BODHAN_OCR_KEY", "BODHAN_TRANSLATE_KEY",
            "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY"} <= declared.keys()
    assert not any(declared.values())
    for directory in ("sample_data", "outputs"):
        assert (RECIPE / directory / ".gitkeep").is_file()
