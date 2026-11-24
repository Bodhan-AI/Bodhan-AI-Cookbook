# Phase 3 validation evidence

Current local validation after the installed-wheel check. These are engineering results, not hosted model-quality results.

| Check | Status | Exit code |
|---|---|---:|
| recipe_tests | pass | 0 |
| recipe_lint | pass | 0 |
| cookbook_recipe | pass | 0 |
| cookbook_rules | pass | 0 |
| cookbook_tests | fail | 1 |
| cookbook_lint | pass | 0 |
| cookbook_validate | fail | 1 |
| make_check | unavailable | None |
| container | unavailable | None |

The harness suite passed **54 tests**. Cookbook tests remain **30 passed, 2 failed**: existing AndroidSpeak structure and unavailable Unix cp. All-recipe validation also fails on AndroidSpeak. GNU make and Docker are unavailable; no full make check or container build is claimed.

Recipe hash: `19604f0b9cea14aa024f31765e64585d0c0ccfd48c296219bb659343e3412129`.

Command logs and hashes are retained in git-ignored `outputs/checks-after-install/checks.json` and its logs directory.

The [installed-wheel check](installation_validation.md) passed in a fresh isolated runtime venv. The [offline benchmark](offline_benchmark.json) verified complete fixture coverage and zero-network, identical-report replay. Neither is live model quality evidence.
