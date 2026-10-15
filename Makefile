# ODC v4 — Makefile
# Full lifecycle: install, test, lint, run, build, clean, audit.

.PHONY: help install install-dev install-test \
        test test-unit test-integration test-llm test-cognitive test-adversarial test-deep \
        test-browser test-real test-audit \
        lint lint-fix format type-check \
        run server daemon cli \
        build dist sdist wheel \
        docker docker-build docker-run docker-stop \
        docs docs-serve \
        audit audit-pdf audit-full \
        clean clean-pyc clean-cache clean-data clean-build clean-venv \
        setup-deps setup-playwright setup-ollama \
        all check ci

# === Config ===
PYTHON       := python3
PIP          := $(PYTHON) -m pip
PYTEST       := $(PYTHON) -m pytest
RUFF         := $(PYTHON) -m ruff
MYPY         := $(PYTHON) -m mypy
BLACK        := $(PYTHON) -m black
PYTHONPATH   := .
DATA_DIR     := ./.odc_data
VENV         := .venv

# === Help ===
help: ## Show this help
	@echo "ODC v4 — production agent"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === Install ===
install: ## Install runtime deps only
	$(PIP) install -r requirements.txt
	@echo "✓ Runtime deps installed"

install-dev: ## Install dev + runtime deps
	$(PIP) install -r requirements-dev.txt
	@echo "✓ Dev + runtime deps installed"

install-test: ## Install test deps
	$(PIP) install -r requirements-dev.txt
	$(PIP) install pytest-benchmark hypothesis
	@echo "✓ Test deps installed"

install-editable: ## Install package in editable mode
	$(PIP) install -e ".[dev,test]"

# === Setup ===
setup: install install-playwright ## Full setup (deps + browser)
	@echo "✓ ODC v4 ready"
	@echo ""
	@echo "Next: cp .env.example .env && edit API keys"
	@echo "Then: make test"

setup-playwright: ## Install Playwright browsers
	$(PYTHON) -m playwright install chromium --with-deps
	@echo "✓ Playwright Chromium ready"

setup-ollama: ## Setup local Ollama brain (optional)
	@if command -v ollama >/dev/null 2>&1; then \
		echo "Ollama already installed: $$(ollama --version)"; \
	else \
		echo "Install from: https://ollama.ai/download"; \
	fi

# === Test ===
test: test-unit test-integration ## Run all unit + integration tests

test-unit: ## Run unit tests (fast)
	$(PYTEST) tests/ -q -m "not slow and not integration and not llm and not browser and not network" -p no:cacheprovider

test-integration: ## Run integration tests
	$(PYTEST) tests/ -q -m "integration" -p no:cacheprovider

test-llm: ## Run tests that use REAL LLM (requires NVIDIA_API_KEY)
	$(PYTEST) tests/test_cognitive_suite.py tests/test_audit_adversarial.py tests/test_self_heal_real_llm.py -q -p no:cacheprovider -W ignore

test-cognitive: ## Run cognitive validation suite
	$(PYTEST) tests/test_cognitive_suite.py -q -p no:cacheprovider -W ignore

test-adversarial: ## Run adversarial audit
	$(PYTEST) tests/test_audit_adversarial.py -q -p no:cacheprovider

test-deep: ## Run deep audit
	$(PYTEST) tests/test_deep_audit.py -q -p no:cacheprovider

test-browser: ## Run browser tests (requires playwright)
	$(PYTEST) tests/ -q -m "browser" -p no:cacheprovider

test-real: test-self-heal-real test-growth test-browser-xprocess ## Run real-LLM demos

test-self-heal-real: ## Real 550B self-heal test
	$(PYTHON) test_self_heal_real_llm.py

test-growth: ## Growth engine test
	$(PYTHON) test_growth.py

test-browser-xprocess: ## Cross-process browser persistence
	$(PYTHON) test_browser_xprocess.py

test-coverage: ## Run with coverage
	$(PYTEST) tests/ --cov=odc --cov-report=term-missing --cov-report=html -p no:cacheprovider

# === Lint / Format ===
lint: ## Run ruff linter
	$(RUFF) check odc tests

lint-fix: ## Auto-fix lint issues
	$(RUFF) check --fix odc tests

format: ## Format with black + ruff
	$(BLACK) odc tests
	$(RUFF) check --fix odc tests

type-check: ## Run mypy
	$(MYPY) odc

# === Run ===
run: server ## Alias for server

server: ## Start the web server
	$(PYTHON) -m odc.server

server-prod: ## Start in production mode (daemon)
	$(PYTHON) -m odc.daemon

daemon: server-prod ## Start daemon

cli: ## Run the CLI
	$(PYTHON) -m odc.cli

repl: ## Start REPL
	$(PYTHON) -m odc.repl

# === Build ===
build: clean-build ## Build sdist + wheel
	$(PYTHON) -m build

sdist: ## Build sdist only
	$(PYTHON) -m build --sdist

wheel: ## Build wheel only
	$(PYTHON) -m build --wheel

dist: build ## Alias for build

# === Docker ===
docker: docker-build ## Alias for build

docker-build: ## Build Docker image
	docker build -t odc-v4:latest .

docker-run: ## Run Docker container
	docker run -d --name odc-v4 \
		-p 8000:8000 \
		-v $(PWD)/.odc_data:/app/.odc_data \
		--env-file .env \
		odc-v4:latest

docker-stop: ## Stop Docker container
	docker stop odc-v4 || true
	docker rm odc-v4 || true

# === Docs ===
docs: ## Build Sphinx docs
	cd docs && $(PYTHON) -m sphinx -b html source build

docs-serve: ## Serve docs locally
	cd docs/build/html && $(PYTHON) -m http.server 8080

# === Audit ===
audit: ## Run cognitive + adversarial + deep audit
	$(PYTEST) tests/test_cognitive_suite.py tests/test_audit_adversarial.py tests/test_deep_audit.py -q -p no:cacheprovider -W ignore

audit-pdf: ## Regenerate PDF audit
	$(PYTHON) -c "from odc.audit.pdf import generate; generate('docs/AUDIT.pdf')"

audit-full: audit test-coverage audit-pdf ## Full audit suite + coverage + PDF

# === Clean ===
clean: clean-pyc clean-cache clean-build ## Clean artifacts
	@echo "✓ Cleaned"

clean-pyc: ## Remove Python cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete

clean-cache: ## Remove pytest cache
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage

clean-build: ## Remove build artifacts
	rm -rf build dist *.egg-info

clean-data: ## Remove runtime data
	rm -rf $(DATA_DIR) data runs logs

clean-venv: ## Remove venv
	rm -rf $(VENV)

clean-all: clean clean-data clean-venv ## Remove EVERYTHING (full reset)

# === Composite ===
all: install-dev test lint ## Install + test + lint

check: lint type-check test ## Run all checks (no install)

ci: lint type-check test-unit ## CI checks

.DEFAULT_GOAL := help
