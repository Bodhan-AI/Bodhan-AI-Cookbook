#!/usr/bin/env bash
# No API calls or secrets required. Run on a Docker-capable host from recipe root.
set -euo pipefail
docker version
docker build --progress=plain -t bodhan-language-task-agent:verification .
docker run --rm bodhan-language-task-agent:verification --help
# Real external MCP SDK and safety tests run inside the built image after adding dev deps.
docker run --rm --entrypoint sh -v "$PWD/tests:/checks/tests:ro" bodhan-language-task-agent:verification -c 'uv sync --frozen && python -m pytest -c /app/pyproject.toml -o cache_dir=/tmp/bodhan-pytest-cache /checks/tests -q && python -m ruff check src /checks/tests'
