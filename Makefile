.PHONY: install test help

install:
	python -m pip install -e .[dev]

test:
	python -m pytest

help:
	phik --help
