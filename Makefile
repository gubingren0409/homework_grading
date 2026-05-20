.PHONY: help format lint test coverage clean install-dev check-all

help:
	@echo "Available commands:"
	@echo "  make install-dev    Install development dependencies"
	@echo "  make format         Format code with black and isort"
	@echo "  make lint           Run linting checks (flake8, mypy, bandit)"
	@echo "  make test           Run tests with coverage"
	@echo "  make coverage       Generate and open coverage report"
	@echo "  make check-all      Run all checks (format, lint, test)"
	@echo "  make clean          Remove generated files"

install-dev:
	pip install --upgrade pip
	pip install black isort flake8 mypy bandit safety pytest pytest-cov pytest-asyncio pytest-mock
	pip install -r requirements.txt

format:
	@echo "Running black..."
	black src tests scripts
	@echo "Running isort..."
	isort src tests scripts
	@echo "Code formatting complete!"

lint:
	@echo "Running flake8..."
	flake8 src tests scripts --count --statistics
	@echo "Running mypy..."
	mypy src --ignore-missing-imports --no-strict-optional --check-untyped-defs || true
	@echo "Running bandit..."
	bandit -r src -ll || true
	@echo "Linting complete!"

test:
	@echo "Running tests with coverage..."
	pytest tests/ -v --cov=src --cov-report=term-missing --cov-report=html

coverage:
	@echo "Generating coverage report..."
	pytest tests/ --cov=src --cov-report=html
	@echo "Opening coverage report..."
	python -m webbrowser htmlcov/index.html

check-all: format lint test
	@echo "All checks passed!"

clean:
	@echo "Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf  + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage coverage.xml bandit-report.json
	@echo "Cleanup complete!"
