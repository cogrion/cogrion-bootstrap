.DEFAULT_GOAL := help

.PHONY: help install test fmt

help:
	@echo "make install  — install dependencies and pre-commit hooks"
	@echo "make test     — run pytest"
	@echo "make fmt      — run black formatter"

install:
	uv sync
	uv run pre-commit install

test:
	uv run pytest

fmt:
	uv run black .
