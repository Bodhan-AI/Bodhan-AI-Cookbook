# Installed-wheel validation

The offline installation check passed against the current recipe. Full evidence: [installation_validation.json](installation_validation.json).

- Build backend pinned and used: setuptools 80.9.0.
- Built from explicit staged package source with indexes and dependency resolution disabled.
- Wheel source matched the staged source; all RECORD checksums and sizes matched.
- Wheel contents excluded private configuration, generated audio, reports and caches. Configured key values were checked without being printed.
- Installed offline into a fresh venv with system/user site packages disabled.
- Imported the installed package at `Lib/site-packages/bodhan_eval/__init__.py` using Python -I from a temporary directory outside the checkout.
- Installed CLI assessed all nine supplied fixture items across Hindi, Tamil and Bengali.
- Replay produced identical report artifact hashes and zero harness network attempts/reservations.

Wheel SHA256: `5e32d9a9271397248563e93dc80c6bfffa299de7c9684521210453b1d8e9ff09`.

The reproducible command is `python -m bodhan_eval.installation --out outputs/installation-check --env-file ../../../.env.quality_harness`. Use a fresh output directory and install the pinned build backend in the build interpreter first.

This used an already installed pinned backend with build isolation disabled. It does not establish backend downloads from a clean clone, Docker success, hosted API compatibility or semantic quality. The numeric/string fixture is engineering test data, not Bodhan-generated demo output or human validation. Full wheel, venv and logs remain local under git-ignored `outputs/installation-final/`; the JSON contains hashes and metadata only.
