# ──────────────────────────────────────────────────────────────────────────────
# Test runner Makefile
# Usage:
#   make install       install all test dependencies + Playwright browser
#   make unit          run unit tests only (fast, no I/O)
#   make api           run API tests only (Flask test client + fakeredis)
#   make e2e           run E2E tests only (Playwright, headless)
#   make e2e-headed    run E2E tests with visible browser
#   make test          run all tests
#   make coverage      run all tests and produce HTML coverage report
#   make fast          unit + api (no browser needed — good for CI pre-commit)
# ──────────────────────────────────────────────────────────────────────────────

.PHONY: install unit api e2e e2e-headed test coverage fast clean

PYTEST      = python -m pytest
COV_OPTS    = --cov=. --cov-report=html --cov-report=term-missing \
              --cov-omit="tests/*,*/__init__.py"
PARALLEL    = -n auto          # requires pytest-xdist; remove if not installed

# ── Install ───────────────────────────────────────────────────────────────────
install:
	pip install -r requirements-test.txt
	playwright install chromium

# ── Unit tests ────────────────────────────────────────────────────────────────
unit:
	$(PYTEST) tests/unit/ -m unit $(PARALLEL)

# ── API tests ─────────────────────────────────────────────────────────────────
api:
	$(PYTEST) tests/api/ -m api $(PARALLEL)

# ── E2E tests ─────────────────────────────────────────────────────────────────
e2e:
	$(PYTEST) tests/e2e/ -m e2e --browser chromium

e2e-headed:
	$(PYTEST) tests/e2e/ -m e2e --browser chromium --headed --slowmo 150

# ── All tests ─────────────────────────────────────────────────────────────────
test:
	$(PYTEST) tests/

# ── Fast (no browser) ─────────────────────────────────────────────────────────
fast:
	$(PYTEST) tests/unit/ tests/api/ $(PARALLEL)

# ── Coverage ──────────────────────────────────────────────────────────────────
coverage:
	$(PYTEST) tests/unit/ tests/api/ $(COV_OPTS)
	@echo "\nCoverage report: htmlcov/index.html"

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:
	rm -rf .pytest_cache htmlcov .coverage __pycache__
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
