.PHONY: install test lint run up down
install:
	python -m pip install -e ".[dev]"
test:
	pytest -q
lint:
	ruff check .
run:
	uvicorn scalpforge_api.main:app --reload --app-dir apps/api
up:
	docker compose up --build
down:
	docker compose down
