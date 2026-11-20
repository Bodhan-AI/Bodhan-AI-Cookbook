#!/usr/bin/env bash
# Publish the web UI as its own GitHub repo, ready for Streamlit Community Cloud:
#   deploy/deploy.sh <github-user>/<repo>
# Community Cloud reads .streamlit/config.toml only from a repo's root, so the app goes at the root.
# Then on share.streamlit.io: New app -> this repo, branch main, file app.py.
set -euo pipefail
REPO=${1:?usage: deploy/deploy.sh <github-user>/<repo>}
cd "$(dirname "$0")/.."
STAGE=$(mktemp -d)
cp -r src app.py .streamlit pyproject.toml deploy/packages.txt deploy/README.md "$STAGE"/
find "$STAGE" \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} +
# Same pins, but torch as its exact CPU wheel: Community Cloud installs with uv, which won't mix
# packages from the PyTorch index and PyPI. Community Cloud is Linux x86_64; a marker per Python
# version picks the matching wheel, whatever version the app is set to.
{
  grep -vE '^(-e \.|--extra-index-url|torch==|#)' requirements.txt
  for v in 312 313 314; do  # our pins need Python 3.12 or newer
    echo "torch @ https://download.pytorch.org/whl/cpu/torch-2.15.0%2Bcpu-cp$v-cp$v-manylinux_2_28_x86_64.whl ; python_version == \"${v:0:1}.${v:1}\""
  done
  grep '^streamlit==' requirements-app.txt
} > "$STAGE/requirements.txt"
cd "$STAGE"
git init -q -b main && git add -A && git commit -qm "Deploy video dubbing web UI"
gh repo view "$REPO" >/dev/null 2>&1 || gh repo create "$REPO" --public --description "Dub English videos into Indian languages with Bodhan's Indic models (web UI)"
git push -q --force "https://github.com/$REPO.git" main
echo "Pushed to https://github.com/$REPO. Deploy it at https://share.streamlit.io (file: app.py)."
