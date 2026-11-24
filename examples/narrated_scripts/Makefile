PYTHON ?= python3
VENV ?= .venv

.PHONY: setup
setup:
	$(PYTHON) -m venv "$(VENV)"
	"$(VENV)/bin/python" -m pip install -r requirements-build.txt
	"$(VENV)/bin/python" -m pip install --no-build-isolation -e '.[test]'
