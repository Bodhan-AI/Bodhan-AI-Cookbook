"""Build locally and prove an offline wheel install in a fresh isolated venv."""

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

from .artifacts import file_hash, implementation_hash, recipe_hash, recipe_root
from .benchmark import hashes, validate_assessment
from .models import load_environment


def backend_requirement(text):
    match = re.search(r'(?m)^requires\s*=\s*\["setuptools==([0-9.]+)"\]\s*$', text)
    if not match or 'build-backend = "setuptools.build_meta"' not in text:
        raise ValueError("installation check requires an exactly pinned setuptools backend")
    return match[1]


def verify_wheel(path, expected_sources, forbidden_values=()):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("duplicate or corrupt wheel members")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        record_names = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(metadata_names) != 1 or len(record_names) != 1:
            raise ValueError("wheel must have one METADATA and RECORD")
        metadata_root = str(PurePosixPath(metadata_names[0]).parent)
        sources = {}
        for name in names:
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name:
                raise ValueError("unsafe wheel member path")
            package_source = (
                len(member.parts) >= 2 and member.parts[0] == "bodhan_eval" and name.endswith(".py") and not any(p.startswith(".") for p in member.parts)
            )
            metadata_file = str(member.parent) == metadata_root and member.name in {"METADATA", "WHEEL", "RECORD", "top_level.txt", "entry_points.txt"}
            if not package_source and not metadata_file:
                raise ValueError("private, generated or unexpected file in wheel")
            content = archive.read(name)
            if any(value and value.encode("utf-8") in content for value in forbidden_values):
                raise ValueError("private configuration value found in wheel")
            if package_source:
                sources[name] = hashlib.sha256(content).hexdigest()
        if sources != expected_sources or not {"bodhan_eval/__main__.py", "bodhan_eval/__init__.py"}.issubset(sources):
            raise ValueError("wheel source does not match staged source")
        entries = list(csv.reader(io.StringIO(archive.read(record_names[0]).decode("utf-8"))))
        if any(len(entry) != 3 for entry in entries) or len(entries) != len(names) or {entry[0] for entry in entries} != set(names):
            raise ValueError("RECORD population does not match wheel")
        for name, checksum, size in entries:
            if name == record_names[0]:
                if checksum or size:
                    raise ValueError("RECORD cannot hash itself")
                continue
            content = archive.read(name)
            encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
            if checksum != "sha256=" + encoded or size != str(len(content)):
                raise ValueError("RECORD checksum or size mismatch")
        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
        if metadata.get("Name") != "bodhan-eval" or metadata.get_all("Requires-Dist"):
            raise ValueError("unexpected distribution or runtime dependencies")
    return {
        "sha256": file_hash(path),
        "members": sorted(names),
        "source_files": len(sources),
        "distribution": metadata.get("Name"),
        "version": metadata.get("Version"),
        "record_verified": True,
    }


def command(argv, cwd, log, environment, timeout=120):
    result = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True, timeout=timeout)
    log.write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise ValueError(f"installation command failed; inspect {log.name}")
    return result.stdout


def run(output, dataset, langs, env_file):
    root, output, dataset = recipe_root(), Path(output).resolve(), Path(dataset).resolve()
    if output.exists():
        raise ValueError("use a fresh installation-check output directory")
    pin = backend_requirement((root / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        installed_backend = importlib.metadata.version("setuptools")
    except importlib.metadata.PackageNotFoundError:
        raise ValueError("install the pinned setuptools backend in the build interpreter first") from None
    if installed_backend != pin:
        raise ValueError("installed build backend differs from pinned version; install the exact build dependency first")
    initial_recipe_hash = recipe_hash()
    output.mkdir(parents=True)
    staged = output / "staged"
    staged.mkdir()
    for name in ("pyproject.toml", "requirements.txt", "README.md"):
        shutil.copyfile(root / name, staged / name)
    expected = {}
    for source in sorted((root / "src" / "bodhan_eval").rglob("*.py")):
        if source.is_symlink():
            raise ValueError("symlinked source is not supported by the installation check")
        relative = source.relative_to(root / "src")
        destination = staged / "src" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        expected[relative.as_posix()] = file_hash(source)
    environment = dict(os.environ)
    environment.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    environment.pop("PYTHONPATH", None)
    environment.pop("PIP_FIND_LINKS", None)
    wheels = output / "wheels"
    wheels.mkdir()
    command(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "--no-index", "--no-cache-dir", "--wheel-dir", str(wheels), "."],
        staged,
        output / "build.log",
        environment,
    )
    built = list(wheels.glob("*.whl"))
    if len(built) != 1:
        raise ValueError("expected exactly one built wheel")
    private = load_environment(env_file)
    forbidden = [private.get(name, "") for name in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY", "LLM_API_KEY")]
    wheel = verify_wheel(built[0], expected, forbidden)
    isolated = output / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(isolated)
    python = isolated / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    working = Path(tempfile.mkdtemp(prefix="bodhan-installed-check-"))
    try:
        command(
            [str(python), "-I", "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir", str(built[0])],
            working,
            output / "install.log",
            environment,
        )
        identity_script = (
            "import bodhan_eval,json,site,sys; "
            "print(json.dumps({'module':bodhan_eval.__file__,'prefix':sys.prefix,"
            "'base_prefix':sys.base_prefix,'user_site':site.ENABLE_USER_SITE}))"
        )
        identity = json.loads(command([str(python), "-I", "-c", identity_script], working, output / "import.log", environment))
        module = Path(identity["module"]).resolve()
        if not module.is_relative_to(isolated) or identity["prefix"] == identity["base_prefix"] or identity["user_site"] is not False:
            raise ValueError("import is not isolated to the fresh virtual environment")
        cfg = (isolated / "pyvenv.cfg").read_text(encoding="utf-8")
        if "include-system-site-packages = false" not in cfg:
            raise ValueError("virtual environment inherits system packages")
        reports = []
        for name in ("first", "replay"):
            report = output / name
            command(
                [
                    str(python),
                    "-I",
                    "-m",
                    "bodhan_eval",
                    "--dataset",
                    str(dataset),
                    "--langs",
                    langs,
                    "--out",
                    str(report),
                    "--offline",
                    "--cache-dir",
                    str(output / "cache"),
                    "--max-cost-inr",
                    "0",
                ],
                working,
                output / f"{name}.log",
                environment,
            )
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (report / "items.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            if not validate_assessment(summary, rows, require_live=False)["complete"] or summary["implementation_sha256"] != implementation_hash():
                raise ValueError("installed CLI did not assess the fixture using the expected implementation")
            reports.append(hashes(report))
        execution = json.loads((output / "cache" / "last_execution.json").read_text(encoding="utf-8"))
        if reports[0] != reports[1] or execution["network_attempts"] != 0 or execution["reserved_inr_including_failed_attempts"] != 0:
            raise ValueError("installed CLI replay is not deterministic and zero-network")
    finally:
        # The isolated working directory should stay empty. Never recursively
        # remove anything unexpectedly created there.
        if working.is_relative_to(Path(tempfile.gettempdir()).resolve()) and not any(working.iterdir()):
            working.rmdir()
    if recipe_hash() != initial_recipe_hash:
        raise ValueError("recipe changed during installation validation")
    result = {
        "version": 1,
        "passed": True,
        "recipe_sha256": initial_recipe_hash,
        "implementation_sha256": implementation_hash(),
        "dataset_sha256": file_hash(dataset),
        "backend": {"name": "setuptools", "pinned": pin, "used": installed_backend},
        "wheel": wheel,
        "installed_module": module.relative_to(isolated).as_posix(),
        "inherited_site_packages": False,
        "isolated_imports": True,
        "artifact_sha256": reports[0],
        "zero_network_replay": True,
        "logs": {p.name: file_hash(p) for p in sorted(output.glob("*.log"))},
        "scope": "offline staged wheel build with installed pinned backend; fresh-venv install and deterministic CLI smoke, not Docker or hosted quality",
    }
    (output / "installation.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dataset", default="data/benchmark_smoke.jsonl")
    parser.add_argument("--langs", default="hi,ta,bn")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    try:
        result = run(args.out, args.dataset, args.langs, args.env_file)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Installation validation failed: {exc}\n")
    print(f"Fresh-venv wheel validation passed: {result['passed']}; evidence: {Path(args.out) / 'installation.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
