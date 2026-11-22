"""Capture real local validation results without installs, publishing or paid calls."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .artifacts import file_hash, recipe_hash, recipe_root


def execute(name, command, cwd, output, environment, timeout=120, unavailable=None):
    start = time.monotonic()
    log = output / "logs" / f"{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if unavailable:
        status, code, content = "unavailable", None, unavailable
    else:
        try:
            completed = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, timeout=timeout)
            code = completed.returncode
            status = "pass" if code == 0 else "fail"
            content = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            status, code, content = "timeout", None, "Check exceeded its timeout; no success is claimed."
        except OSError:
            status, code, content = "unavailable", None, "Could not launch required tool."
    log.write_text(content + "\n", encoding="utf-8")
    return {
        "name": name,
        "status": status,
        "exit_code": code,
        "seconds": time.monotonic() - start,
        "log": log.relative_to(output).as_posix(),
        "log_sha256": file_hash(log),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", required=True)
    parser.add_argument("--with-docker", action="store_true", help="build the Dockerfile, running its tests, if Docker is installed")
    parser.add_argument("--with-make", action="store_true", help="run exact make check; its test target may install pinned development dependencies")
    args = parser.parse_args(argv)
    root = recipe_root()
    cookbook = root.parents[1]
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "checks.json").exists():
        parser.error("use a fresh output directory to preserve prior validation evidence")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "src")
    initial_hash = recipe_hash()
    commands = [
        ("recipe_tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], root),
        ("recipe_lint", [sys.executable, "-m", "ruff", "check", "src", "tests", "--select", "E,F,I"], root),
        ("cookbook_recipe", [sys.executable, "scripts/validate_recipe.py", "examples/quality_harness"], cookbook),
        ("cookbook_rules", [sys.executable, "scripts/bodhan_rules.py", "--check"], cookbook),
        ("cookbook_tests", [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"], cookbook),
        ("cookbook_lint", [sys.executable, "-m", "ruff", "check", "scripts/", "tests/"], cookbook),
        ("cookbook_validate", [sys.executable, "scripts/ci_validate.py", "--all"], cookbook),
    ]
    checks = []
    for name, command, directory in commands:
        check = execute(name, command, directory, output, environment)
        checks.append(check)
        print(f"{name}: {check['status']}", flush=True)
    make = shutil.which("make")
    check = execute(
        "make_check",
        [make or "make", "check"],
        cookbook,
        output,
        environment,
        unavailable=None if make and args.with_make else "Exact make check not executed; use --with-make in an environment with GNU make.",
    )
    checks.append(check)
    docker = shutil.which("docker")
    check = execute(
        "container",
        [docker or "docker", "build", "-t", "bodhan-quality-harness:validation", "."],
        root,
        output,
        environment,
        timeout=600,
        unavailable=None if docker and args.with_docker else "Docker build not executed; use --with-docker with an available Docker daemon.",
    )
    checks.append(check)
    final_hash = recipe_hash()
    result = {
        "version": 1,
        "recipe_sha256": final_hash,
        "source_unchanged": initial_hash == final_hash,
        "all_passed": initial_hash == final_hash and all(c["status"] == "pass" for c in checks),
        "checks": checks,
        "note": (
            "Actual local exit codes and log hashes. Default checks do not install dependencies; "
            "--with-make invokes the upstream install-bearing target. No model calls made."
        ),
    }
    (output / "checks.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"All checks passed: {result['all_passed']}; evidence: {output / 'checks.json'}")
    return 0 if result["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
