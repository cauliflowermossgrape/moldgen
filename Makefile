VENV ?= .venv
PYTHON := $(VENV)/bin/python

.PHONY: dev test venv

$(VENV)/bin/activate: pyproject.toml
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"
	touch $(VENV)/bin/activate

venv: $(VENV)/bin/activate

dev: venv
	$(PYTHON) -m uvicorn moldgen.main:app --reload --port 8000

test: venv
	$(PYTHON) -m pytest
