.PHONY: up down logs migrate test lint typecheck format audit demo
up:
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f
migrate:
	docker compose run --rm migrate
test:
	python -m pytest -q
lint:
	python -m ruff check .
typecheck:
	python -m mypy app
format:
	python -m ruff format .
audit:
	python -m bandit -r app -q
	python -m pip_audit -r requirements.lock --disable-pip --no-deps
demo:
	python scripts/demo.py

