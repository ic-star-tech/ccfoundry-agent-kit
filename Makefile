PYTHON ?= python3
VENV ?= .venv

.PHONY: setup-python run-example-agent run-dev-board-api run-dev-board-web

setup-python:
	$(PYTHON) -m venv $(VENV)
	. $(VENV)/bin/activate && pip install -U pip && \
	pip install -e packages/python-sdk && \
	pip install -e examples/me_agent && \
	pip install -e apps/agent-dev-board-api

run-example-agent:
	uvicorn me_agent_example.app:app --app-dir examples/me_agent/src --reload --port 8085

run-dev-board-api:
	uvicorn agent_dev_board_api.app:app --app-dir apps/agent-dev-board-api/src --reload --port 8090

run-dev-board-web:
	cd apps/agent-dev-board-web && npm install && npm run dev
