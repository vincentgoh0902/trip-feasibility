#!/usr/bin/env bash
# Everything that must pass before a refactor (or any change that shouldn't alter behaviour)
# is committed. Stops at the first failure.
#
#   e2e/run.sh                 # all steps
#   e2e/run.sh --no-browser    # skip the full stack and Playwright (no LLM calls at all)
#
# 1. backend: uv sync, ruff, migrations (upgrade + check), pytest
# 2. behaviour snapshot: dev gold-mode eval + the example, from the cache, compared with
#    data/evals/snapshot.json (record it first: backend/scripts/snapshot.py --save)
# 3. frontend: generated API types current, eslint, typecheck, vitest
# 4. full stack: docker compose up --build, /health
# 5. Playwright (frontend/e2e): landing → demo → a live demo check through the Next.js proxy,
#    at desktop and 375 px. Calls the real data sources and, with ANTHROPIC_API_KEY in .env,
#    the fix model (a few Haiku calls).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_URL="http://localhost:${API_HOST_PORT:-8020}"
browser=1
[[ "${1:-}" == "--no-browser" ]] && browser=0

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

step "backend: lint, migrations, tests"
docker compose -f "$ROOT/compose.yaml" up -d db
cd "$ROOT/backend"
uv sync --quiet
uv run ruff check .
uv run ruff format --check .
uv run alembic upgrade head
uv run alembic check
uv run pytest -q

step "behaviour snapshot"
uv run python scripts/snapshot.py 2>&1 | grep -v '^HTTP Request'

step "frontend: types, lint, typecheck, unit tests"
cd "$ROOT/frontend"
npm run --silent gen:api
git diff --exit-code src/lib/api/schema.d.ts
npm run --silent lint
npm run --silent typecheck
npm test --silent

if [[ $browser == 0 ]]; then
  step "skipped: full stack and browser"
  exit 0
fi

step "full stack: build and health"
docker compose -f "$ROOT/compose.yaml" up -d --build
for _ in $(seq 1 60); do
  curl -fsS "$API_URL/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "$API_URL/health"
echo

step "browser: Playwright"
API_INTERNAL_URL="$API_URL" npm run --silent e2e

step "all passed"
