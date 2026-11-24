.PHONY: check test lint rules validate

check: test lint rules validate

test:
	pytest -q

lint:
	@python -c "import shutil, subprocess, sys; sys.exit(subprocess.call(['ruff', 'check', 'src/', 'scripts/', 'tests/']) if shutil.which('ruff') else 0)"

rules:
	bash scripts/guardrails.sh

validate:
	@python -c "from pathlib import Path; req = ['README.md', 'requirements.txt', '.env.example', '.gitignore', 'sample_data/demo.jsonl', 'outputs/.gitkeep']; miss = [p for p in req if not Path(p).exists()]; assert not miss, f'Missing: {miss}'; readme = Path('README.md').read_text(encoding='utf-8'); assert '## Setup' in readme and '## Run' in readme and '## How it works' in readme; print('validate: PASS')"
