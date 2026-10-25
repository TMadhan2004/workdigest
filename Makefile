# WorkDigest monorepo tasks. `make check` runs everything CI runs.
.PHONY: help setup lint typecheck test coverage catalog catalog-check db-test app-check e2e-fixture public-check tokens tokens-check web web-check site wheel check clean

RECORDER := recorder
UV := uv --directory $(RECORDER)

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup: ## Install the recorder's dev environment
	$(UV) sync

lint: ## Ruff lint and format check
	$(UV) run ruff format --check src tests
	$(UV) run ruff check src tests

typecheck: ## mypy --strict
	$(UV) run mypy

test: ## Run all recorder tests
	$(UV) run pytest

coverage: ## Tests with coverage gates (90% overall, 95% on redaction, grounding, compliance)
	$(UV) run pytest --cov --cov-report=term-missing
	$(UV) run coverage report --include='src/workdigest/redact/*,src/workdigest/digest/grounding.py,src/workdigest/digest/compliance.py' --fail-under=95

catalog: ## Regenerate docs/test-cases.md from test docstrings
	python3 scripts/gen_test_catalog.py

catalog-check: ## Fail if the test-case catalog is stale
	python3 scripts/gen_test_catalog.py --check

app-check: ## Flutter app: analyze (zero issues), all tests, 85% line coverage
	python3 scripts/embed_app_fixtures.py --check
	cd app && flutter analyze --no-pub
	cd app && flutter test --no-pub --coverage
	python3 scripts/check_lcov.py app/coverage/lcov.info 85

e2e-fixture: ## Re-record app/test/fixtures/e2e_cloud.json from the recorder's end-to-end run
	WORKDIGEST_WRITE_E2E_FIXTURE=1 $(UV) run pytest tests/e2e -q
	python3 scripts/embed_app_fixtures.py

public-check: ## Fail if a tracked file contains real names, internal repos or project ids
	python3 scripts/check_public.py

tokens: ## Regenerate the CSS and Dart design tokens from design/tokens.json
	python3 scripts/gen_tokens.py

tokens-check: ## Fail if the generated design tokens are stale
	python3 scripts/gen_tokens.py --check

web: ## Build the setup wizard into the recorder package
	cd web && npm ci --no-audit --no-fund && npx ng build setup

web-check: tokens-check ## Web: setup wizard and website builds, unit tests, Playwright (desktop + Pixel) with axe
	cd web && npx ng build setup
	cd web && npm run build:site
	cd web && npx ng test setup --watch=false --browsers=ChromeHeadless
	cd web && npx ng test site --watch=false --browsers=ChromeHeadless
	cd web && npx playwright test

site: ## Build the website into web/dist/site/browser (SITE_URL, GITHUB_REPOSITORY, VERSION_JSON optional)
	cd web && npm ci --no-audit --no-fund && npm run build:site

wheel: web ## Build the recorder wheel with the setup pages inside (dist/)
	$(UV) build --wheel

db-test: ## pgTAP tests against the local Supabase stack (`supabase start` first)
	supabase test db

check: public-check lint typecheck coverage catalog-check app-check web-check ## Everything CI runs (cloud tests run when `supabase start` is up)

clean: ## Remove caches
	rm -rf $(RECORDER)/.pytest_cache $(RECORDER)/.mypy_cache $(RECORDER)/.ruff_cache $(RECORDER)/.coverage
