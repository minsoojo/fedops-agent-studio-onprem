.PHONY: install dev-api dev-api-stop dev-web folder-opener-start folder-opener-stop typecheck test build verify

STUDIO_DEV_API_PORT ?= 24369

install:
	npm install
	python3.12 -m venv .venv
	.venv/bin/pip install -r services/studio-api/requirements.txt -r services/studio-runtime/requirements.txt

dev-api: folder-opener-start
	STUDIO_DEV_API_PORT=$(STUDIO_DEV_API_PORT) docker compose -f compose.dev.yml up -d --no-build --pull never studio-api-dev
	@echo "Development API: http://localhost:$(STUDIO_DEV_API_PORT) (container: fedops-agent-studio-dev-api)"

dev-api-stop:
	STUDIO_DEV_API_PORT=$(STUDIO_DEV_API_PORT) docker compose -f compose.dev.yml down

folder-opener-start:
	PYTHONPATH=services/studio-runtime/src .venv/bin/python -m studio_runtime.folder_bridge --workspace $(HOME)/fedops-workspace --token-file $(CURDIR)/.runtime/folder-opener-token --host 0.0.0.0 --port 5602 --daemon --pid-file $(CURDIR)/.runtime/folder-opener.pid --log-file $(CURDIR)/.runtime/folder-opener.log

folder-opener-stop:
	PYTHONPATH=services/studio-runtime/src .venv/bin/python -m studio_runtime.folder_bridge --workspace $(HOME)/fedops-workspace --token-file $(CURDIR)/.runtime/folder-opener-token --stop --pid-file $(CURDIR)/.runtime/folder-opener.pid

dev-web:
	STUDIO_DEV_API_URL=http://127.0.0.1:$(STUDIO_DEV_API_PORT) npm run dev:web

typecheck:
	npm run typecheck

test:
	PYTHONPATH=services/studio-api/src:services/studio-runtime/src .venv/bin/python -m unittest discover -s services/studio-api/tests -p 'test_*.py'
	PYTHONPATH=services/studio-api/src:services/studio-runtime/src .venv/bin/python -m unittest discover -s services/studio-runtime/tests -p 'test_*.py'
	.venv/bin/python -m unittest discover -s scripts/tests -p 'test_*.py'

build:
	npm run build

verify: typecheck test build
