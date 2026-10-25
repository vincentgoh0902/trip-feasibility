# frontend

Next.js (App Router) + Tailwind + shadcn/ui for the Trip Feasibility Checker. See the repo's
`CLAUDE.md` ("Frontend") and `docs/ARCHITECTURE.md` section 6e.

```sh
npm ci
npm run dev            # http://localhost:3000, API from API_INTERNAL_URL (default :8020)
npm run typecheck && npm run lint && npm test
npm run gen:api        # after `uv run python scripts/export_openapi.py` in backend/
```
