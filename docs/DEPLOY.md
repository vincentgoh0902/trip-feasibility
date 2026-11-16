# Deploying trip-feasibility on the homelab

For the **server session** (`ssh homelab`, `cd ~/homelab && claude`). The homelab's own
CLAUDE.md is the source of truth for how projects are added; this file says what this app
needs. Start **private**; going public is Vincent's call, later (bottom of this file).

## Shape: three containers

| Service | Image | Port | Memory (measured locally) | Notes |
|---|---|---|---|---|
| `web` | `frontend/Dockerfile` (Next.js standalone) | `PORT` 3000 | ~46 MB idle | **The entry point.** Serves the pages and proxies `/api/v1/*` to the API |
| `api` | `backend/Dockerfile` (FastAPI) | `PORT` 8000 | ~145 MB idle, ~155 MB after runs | Never reached by browsers, only by `web`. Runs `alembic upgrade head` on every start |
| `db` | `pgvector/pgvector:pg17` | 5432 | ~210 MB | Postgres with pgvector; ~300 MB of review data |

So the Tailscale sidecar fronts `web` on 3000. The API and the database stay on the
project's internal network. If the homelab has no multi-container project type yet, adding
one is the cleaner choice; one combined image (Next.js and uvicorn under a supervisor)
would also work, but the two apps together need more than the 256 MB default.

`compose.yaml` at the repo root is the local version of this shape (it publishes ports on
127.0.0.1 for testing; the server setup publishes none).

## Configuration

`web`:
- `PORT=3000`, `API_INTERNAL_URL=http://api:8000` (the API's address on the internal network).

`api` (`app.env`; secrets never in git):
- `DATABASE_URL=postgresql+asyncpg://trip:<password>@db:5432/trip` with a real password,
  the same one the `db` container gets (`POSTGRES_PASSWORD`, `POSTGRES_USER=trip`,
  `POSTGRES_DB=trip`).
- `ANTHROPIC_API_KEY` (secret): parsing, fixes, closures and the second opinion. Set a
  monthly spend cap in the Anthropic Console. A demo run costs about $0.01 (one or two fix
  calls; no web searches); a signed-in live check about $0.15–0.30.
- `PORT=8000`.
- `FORWARDED_ALLOW_IPS`: the `web` container's address (or the internal subnet), so the
  API trusts the `X-Forwarded-For` that `web` passes on.
- Optional: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` (tracing; no
  traveller text is sent). Not needed yet: `API_JWT_SECRET` (sign-in isn't built; without
  it the signed-in routes answer 503 and the demo works).
- Everything else has working defaults (CLAUDE.md, "Env vars").

`db`: its data directory on `/data` (`projects/trip-feasibility/data/postgres`), so it's
backed up. Nothing else writes there.

## First deploy

1. Add the project (`new-project.sh`, private), with the three services above, from
   `https://github.com/vincentgoh0902/trip-feasibility` (`main`).
2. Start it. The API applies the migrations on start; check `GET /health` on the API
   (`{"status":"ok","database":"ok"}`) and that `web` answers 200 on `/` and `/demo`.
3. **Load the hotel reviews.** Vincent copies the dump from the Mac (from the repo root
   there): `scp data/deploy/reviews.dump homelab:~/reviews.dump` (509 MB; made on the Mac
   with `pg_dump --data-only -Fc -t hotels -t hotel_reviews`). Then, on the server, after
   the migrations have run:
   `docker exec -i <db container> pg_restore -U trip -d trip --data-only < ~/reviews.dump`
   Expect 1,227 hotels and 295,728 reviews
   (`select count(*) from hotels; select count(*) from hotel_reviews;`). Delete the dump
   file afterwards. Restoring into a fresh, migrated database was tested on the Mac.
4. **Warm the demo** (web searches for the three sample itineraries, about $0.30):
   `docker exec <api container> python -m trip_feasibility.demo.warm`
   It prints `closures ok, second_opinion ok` per sample. Demo runs never search; they
   use these lookups, of any age. Rerun it after a sample or the second opinion's prompt
   changes.
5. Try it: open `/demo`, run each sample. Each should finish with problems shown, the
   second opinion's "Confirmed by a web search" / "Worth checking" labels and some fixes.

## Later deploys

From the Mac session, after a push: `ssh homelab '~/homelab/scripts/deploy.sh trip-feasibility'`
(Vincent approves each one).

## Before going public (Vincent decides, in the server session)

- **Client IPs.** The demo is limited to 10 runs an hour per IP, and live runs per user. The
  limit only works if the API sees each visitor's IP: `web` passes on the `X-Forwarded-For`
  it receives and adds none. Check that the public tunnel (Funnel or Cloudflare) sets it;
  without it every visitor shares one limit.
- The Anthropic spend cap is set; no debug mode (none exists); no admin pages (none exist).
- Then the README's "Live demo" line and the GitHub repo's "Website" field get the URL.
