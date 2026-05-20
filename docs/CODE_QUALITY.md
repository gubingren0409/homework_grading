# Code Quality and CI/CD Guide

This document describes the code quality tools and CI/CD workflows configured for this project.

## Quick Start

### Install Development Tools

```bash
make install-dev
```

Or manually:
```bash
pip install black isort flake8 mypy bandit safety pytest pytest-cov
```

### Run All Checks

```bash
make check-all
```

This runs formatting, linting, and tests in sequence.

## Code Quality Tools

### 1. Black (Code Formatting)

Black automatically formats Python code to a consistent style.

**Run:**
```bash
black src tests scripts
```

**Check without modifying:**
```bash
black --check --diff src tests scripts
```

**Configuration:** `pyproject.toml` → `[tool.black]`

### 2. isort (Import Sorting)

isort automatically sorts and organizes imports.

**Run:**
```bash
isort src tests scripts
```

**Check without modifying:**
```bash
isort --check-only --diff src tests scripts
```

**Configuration:** `pyproject.toml` → `[tool.isort]`

### 3. Flake8 (Linting)

Flake8 checks for code style issues and potential bugs.

**Run:**
```bash
flake8 src tests scripts
```

**Configuration:** `.flake8`

**Key checks:**
- Syntax errors (E9, F63, F7, F82)
- Code complexity (max 15)
- Line length (max 120)
- PEP 8 compliance

### 4. Mypy (Type Checking)

Mypy performs static type checking.

**Run:**
```bash
mypy src --ignore-missing-imports --check-untyped-defs
```

**Configuration:** `pyproject.toml` → `[tool.mypy]`

### 5. Bandit (Security Scanning)

Bandit scans for common security issues.

**Run:**
```bash
bandit -r src -ll
```

**Generate report:**
```bash
bandit -r src -ll -f json -o bandit-report.json
```

**Configuration:** `pyproject.toml` → `[tool.bandit]`

### 6. Safety (Dependency Vulnerability Check)

Safety checks for known security vulnerabilities in dependencies.

**Run:**
```bash
safety check
```

## Testing

### Run Tests

```bash
pytest tests/ -v
```

### Run Tests with Coverage

```bash
pytest tests/ --cov=src --cov-report=term-missing --cov-report=html
```

### View Coverage Report

```bash
make coverage
```

Or manually open `htmlcov/index.html` in a browser.

**Configuration:**
- `pytest.ini` - pytest configuration
- `.coveragerc` - coverage configuration
- `pyproject.toml` → `[tool.pytest.ini_options]`

## CI/CD Workflows

### 1. Code Quality Checks (`.github/workflows/code-quality.yml`)

Runs on every PR and push to main/master.

**Checks:**
- Black formatting
- isort import sorting
- Flake8 linting
- Mypy type checking
- Bandit security scanning
- Safety vulnerability check

**Trigger paths:**
- `src/**/*.py`
- `tests/**/*.py`
- `scripts/**/*.py`

### 2. Tests (`.github/workflows/tests.yml`)

Runs tests with coverage on Python 3.11 and 3.12.

**Features:**
- Matrix testing (Python 3.11, 3.12)
- Redis service container
- Coverage reporting
- Codecov integration
- Coverage artifact upload

**Requirements:**
- Minimum 70% code coverage
- All tests must pass on Python 3.12

### 3. Prompt Assets Preflight (`.github/workflows/prompt-assets-preflight.yml`)

Validates prompt configuration files.

**Checks:**
- JSON schema validation
- Jinja2 template syntax
- Variable declarations

## Pre-commit Hooks

The project uses git hooks for validation:

**Location:** `.githooks/pre-commit`

**Checks:**
- Prompt asset validation (for `configs/prompts/*.json` changes)

**Setup:**
```bash
git config core.hooksPath .githooks
```

## Makefile Commands

```bash
make help           # Show available commands
make install-dev    # Install development dependencies
make format         # Format code (black + isort)
make lint           # Run linting checks
make test           # Run tests with coverage
make coverage       # Generate and open coverage report
make check-all      # Run all checks
make clean          # Remove generated files
```

## Configuration Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Black, isort, mypy, pytest, coverage, bandit config |
| `.flake8` | Flake8 linting rules |
| `pytest.ini` | Pytest configuration (legacy, migrating to pyproject.toml) |
| `.coveragerc` | Coverage.py configuration (legacy, migrating to pyproject.toml) |
| `Makefile` | Convenient command shortcuts |

## Best Practices

1. **Before committing:**
   ```bash
   make format
   make lint
   make test
   ```

2. **Fix formatting issues:**
   ```bash
   black src tests scripts
   isort src tests scripts
   ```

3. **Check coverage:**
   - Aim for 70%+ coverage
   - Add tests for new features
   - Use `make coverage` to identify gaps

4. **Security:**
   - Review Bandit warnings
   - Keep dependencies updated
   - Run `safety check` regularly

5. **Type hints:**
   - Add type hints to new code
   - Fix mypy errors when possible
   - Use `# type: ignore` sparingly

## Troubleshooting

### Black and Flake8 conflicts

The configuration is set to avoid conflicts:
- E203, W503, E501 are ignored in Flake8
- Both use 120 character line length

### Import errors in tests

Add to `pyproject.toml`:
```toml
[[tool.mypy.overrides]]
module = "tests.*"
ignore_errors = true
```

### Coverage too low

1. Check `htmlcov/index.html` for uncovered lines
2. Add tests for critical paths
3. Use `# pragma: no cover` for unreachable code

## Resources

- [Black documentation](https://black.readthedocs.io/)
- [isort documentation](https://pycqa.github.io/isort/)
- [Flake8 documentation](https://flake8.pycqa.org/)
- [Mypy documentation](https://mypy.readthedocs.io/)
- [Bandit documentation](https://bandit.readthedocs.io/)
- [pytest documentation](https://docs.pytest.org/)
