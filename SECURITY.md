# Security policy

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

- Vulnerability in a recipe or in this repo's tooling: email **security@bodhan.ai** with the recipe path, a description, and steps to reproduce.
- Vulnerability in the Bodhan API or console itself: email **security@bodhan.ai** as well; the cookbook maintainers will route it.

You will get an acknowledgement within 3 working days.

## Leaked credentials

If you find a real API key anywhere in this repository or its history:

1. Email security@bodhan.ai with the file path and commit.
2. Do not use the key.

If you leaked your **own** key, rotate it in [console.bodhan.ai](https://console.bodhan.ai) first, then open a PR removing it. Assume anything pushed to a public branch has been read.

## What CI checks

Every pull request is scanned for common credential patterns, committed `.env` files, and notebooks with retained outputs. The scan is a safety net, not a substitute for care; see the security section of [CONTRIBUTING.md](CONTRIBUTING.md).
