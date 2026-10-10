.PHONY: up down test lint smoke account

VENV ?= .venv
PY := $(VENV)/bin

up:
	docker compose up -d --build --wait

down:
	docker compose down -v

test:
	cd wallet-service && ../$(PY)/pytest
	cd provider-mock && ../$(PY)/pytest

lint:
	$(PY)/ruff check .
	$(PY)/ruff format --check .

smoke:
	python3 scripts/smoke_test.py

account:
	docker compose exec -T wallet python -m wallet.cli create-account $(or $(NAME),alice)
