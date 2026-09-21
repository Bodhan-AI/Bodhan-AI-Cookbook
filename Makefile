.PHONY: check test lint validate validate-pr rules new-recipe clean-outputs

# Full local gate — the same steps CI runs on every pull request.
check: test lint rules validate

test:
	pip install -r requirements-dev.txt -q
	pytest tests/ -v --tb=short

lint:
	ruff check scripts/ tests/

# Confirms scripts/bodhan_api_rules.json is well-formed and self-consistent.
rules:
	python scripts/bodhan_rules.py --check

# Validates every recipe changed relative to the base branch (default: main).
validate:
	python scripts/ci_validate.py --base-ref $(or $(BASE_REF),main)

validate-pr: validate

# Scaffold a new recipe from examples/TEMPLATE:  make new-recipe NAME=my_recipe
new-recipe:
	python scripts/new_recipe.py $(NAME)

# Strip outputs from every notebook in the repo before committing.
clean-outputs:
	python scripts/clear_notebook_outputs.py
