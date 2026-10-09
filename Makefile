.PHONY: package-release docs-check init install check test-db test-down restart-check rea-check import-check inventory-check rpgle-check cl-check dds-check rules-check retrieval-check semantic-check export-mcp-check analyst-check banking-check compose-check up smoke down
init:
	python3 scripts/init_local.py
install:
	cd backend && uv sync --locked --python 3.12
	cd web && npm ci --ignore-scripts --no-audit --no-fund
test-db:
	docker compose -f compose.test.yaml up --detach --wait --wait-timeout 60
test-down:
	docker compose -f compose.test.yaml down
check: test-db
	cd backend && uv run --locked ruff check .
	cd backend && uv run --locked ruff format --check .
	cd backend && uv run --locked mypy src
	cd backend && LAIP_TEST_DATABASE_URL="$${LAIP_TEST_DATABASE_URL:-postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test}" uv run --locked pytest
	python3 scripts/generate_api_types.py --check
	cd web && npm run check
compose-check: init
	docker compose config --quiet
	python3 scripts/verify_compose.py
up: compose-check
	docker compose up --build --detach --wait --wait-timeout 180
smoke:
	python3 scripts/smoke.py
down:
	docker compose down
restart-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked python scripts/test_restart.py

rea-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked python scripts/test_rea.py

import-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked python scripts/test_import.py

inventory-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_inventory.py backend/tests/test_inventory_store.py --no-cov

rpgle-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_rpgle_*.py --no-cov

cl-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_cl_*.py --no-cov

dds-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_dds_*.py --no-cov

rules-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_rules.py backend/tests/test_rule_store.py backend/tests/test_rule_pipeline.py backend/tests/test_workflows.py --no-cov

retrieval-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_retrieval.py backend/tests/test_context.py backend/tests/test_masking.py backend/tests/test_semantic.py backend/tests/test_settings_ai.py backend/tests/test_private_ai.py --no-cov

semantic-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_private_semantics.py --no-cov

export-mcp-check: test-db
	LAIP_TEST_DATABASE_URL="postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test" uv run --project backend --locked pytest backend/tests/test_exports.py backend/tests/test_mcp.py backend/tests/test_mcp_transport.py backend/tests/test_read_service.py backend/tests/test_read_api.py --no-cov

analyst-check:
	uv run --project backend --locked python scripts/analyst_smoke.py

banking-check: test-db
	@test -f backend/tests/fixtures/banking/oracle.json && test -f backend/tests/fixtures/banking/manifest.json || (echo "Private regression metadata is withheld from the public candidate; use the authorized private development checkout."; exit 2)
	LAIP_TEST_DATABASE_URL="$${LAIP_TEST_DATABASE_URL:-postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test}" uv run --project backend --locked python scripts/test_banking.py
	LAIP_TEST_DATABASE_URL="$${LAIP_TEST_DATABASE_URL:-postgresql://laip_test@127.0.0.1:$${LAIP_TEST_PORT:-55433}/laip_test}" uv run --project backend --locked pytest backend/tests/test_banking_*.py --no-cov --tb=short

docs-check:
	python3 scripts/check_docs.py

package-release: docs-check
	python3 scripts/package_release.py
