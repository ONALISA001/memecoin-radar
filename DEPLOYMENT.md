# Deploying Memecoin Radar to Vercel + Railway

This is the "access from anywhere, permanently" setup: **frontend on Vercel**
(free tier), **backend + Postgres on Railway** (~$5/mo hobby credit usually
covers it). Total hands-on time: **~15 minutes**, no server to manage.

```
browser ──► your-app.vercel.app (Next.js)
                 │  server-side rewrite /api/*   (§22: no secrets in the browser)
                 ▼
           your-api.up.railway.app (FastAPI + scheduler)
                 ▼
           Railway Postgres (persistent history for calibration/backtesting)
```

---

## Step 0 — put the code on GitHub (once)

```bash
cd memecoin-radar
git init && git add -A && git commit -m "memecoin radar"
# Create a new repo on github.com (Private is fine), then:
git remote add origin https://github.com/YOUR_USERNAME/memecoin-radar.git
git push -u origin main
```

## Step 1 — backend on Railway (~5 min)

1. <https://railway.com> → **Sign in with GitHub** → **New Project** →
   **Deploy from GitHub repo** → pick `memecoin-radar`.
2. In the service's **Settings → Build** set **Root Directory: `backend`**
   (Railway finds `backend/railway.toml` + `Dockerfile` automatically).
3. **+ New → Database → PostgreSQL** in the same project.
4. In the API service → **Variables**, add:
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (click the variable-reference helper) |
   | `JWT_SECRET` | any long random string (`openssl rand -hex 32`) |
   | `INGEST_INTERVAL_SECONDS` | `60` (optional) |
   | `RATE_LIMIT_PER_MIN` | `240` (optional) |
5. **Settings → Networking → Generate Domain** → copy the URL, e.g.
   `https://memecoin-radar-api.up.railway.app`.
6. Wait for green. Check `https://<your-domain>/docs` — Swagger should load.
   The first data tick runs on boot; tokens appear within ~1 minute.

> **SQLite alternative:** skip Postgres and add a Railway **Volume** mounted at
> `/data`, then set `DATABASE_URL=sqlite:////data/radar.db`. Cheaper, but Postgres
> survives redeploys/crashes more reliably and matches `docker-compose.yml`.

## Step 2 — frontend on Vercel (~5 min)

1. <https://vercel.com> → **Sign in with GitHub** → **Add New… → Project** →
   import `memecoin-radar`.
2. **Root Directory: `frontend`** (Vercel detects Next.js automatically — leave
   build/install commands at defaults).
3. **Environment Variables** (Production):
   | Variable | Value |
   |---|---|
   | `API_ORIGIN` | `https://memecoin-radar-api.up.railway.app` ← your Railway URL, **no trailing slash** |
4. Deploy. Your site is live at `https://<your-project>.vercel.app`.
5. If you ever change the Railway URL, update `API_ORIGIN` and **Redeploy**
   (API rewrites are baked in at build time).

## Step 3 — optional polish

- **Custom domain:** Vercel → Settings → Domains (free, automatic HTTPS).
- **Demo login:** the UI ships with demo credentials (email `demo@memecoin.radar`,
  password `radar-demo-2026`, auto-created on first boot). For a shared public
  deployment, either keep `DEMO_MODE=1` (read-only analytics is harm-free) or set
  `DEMO_MODE=0` and rotate the password in the DB.
- **Notes (§31):** the app is analytics-only — keep it that way if you share it.

## Updating the site

Both platforms auto-deploy on push: `git push` → Railway rebuilds the API,
Vercel rebuilds the frontend. Schema changes are safe: startup runs
`create_all` + lightweight column migrations automatically.

## Costs (approximate, Sept 2026)

| Piece | Plan | Cost |
|---|---|---|
| Vercel frontend | Hobby | $0 |
| Railway API + Postgres | Hobby (~$5 credit/mo) | typically $0–5 |
| DexScreener market data | public API | $0 (rate-limited, app throttles to ~1 req/s) |

Simulated social providers cost nothing; plugging a real X API later is the only
material operating cost (~$200/mo) — and it's optional by design (mock adapter).
