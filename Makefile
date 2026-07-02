.DEFAULT_GOAL := help

.PHONY: help install test lint

help:
	@echo "make install  — install dependencies and pre-commit hooks"
	@echo "make test     — run pytest"
	@echo "make lint      — run black formatter"

install:
	uv sync
	uv run pre-commit install

test:
	uv run pytest --cov=cogrion_bootstrap --cov-report=term-missing

lint:
	uv run black .
