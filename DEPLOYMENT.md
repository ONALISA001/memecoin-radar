# Deploying Memecoin Radar to Vercel + Railway

This is the "access from anywhere, permanently" setup: **frontend on Vercel or
Netlify** (free tier), **backend + Postgres on Railway** (~$5/mo hobby credit
usually covers it). Total hands-on time: **~15 minutes**, no server to manage.

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
   | `OWNER_KEY` | another long random string — your owner login (`openssl rand -hex 24`) |
   | `DEMO_MODE` | `1` = visitors can browse read-only; `0` = owner only |
   | `INGEST_INTERVAL_SECONDS` | `60` (optional) |
   | `RATE_LIMIT_PER_MIN` | `240` (optional) |
   | `TELEGRAM_BOT_TOKEN` | token from @BotFather (optional — enables Telegram alerts) |
   | `TELEGRAM_CHAT_ID` | channel/chat ID, e.g. `-100…` (the bot must be an admin of the channel) |
5. **Settings → Networking → Generate Domain** → copy the URL, e.g.
   `https://memecoin-radar-api.up.railway.app`.
6. Wait for green. Check `https://<your-domain>/docs` — Swagger should load.
   The first data tick runs on boot; tokens appear within ~1 minute.

> **SQLite alternative:** skip Postgres and add a Railway **Volume** mounted at
> `/data`, then set `DATABASE_URL=sqlite:////data/radar.db`. Cheaper, but Postgres
> survives redeploys/crashes more reliably and matches `docker-compose.yml`.

## Step 2 — frontend on Vercel **or Netlify** (~5 min)

### Option A — Vercel (recommended, zero-config for Next.js)

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

### Option B — Netlify (equivalent free-tier frontend host)

1. <https://netlify.com> → **Sign in with GitHub** → **Add new site → Import an
   existing project** → pick `memecoin-radar`.
2. Set **Base directory: `frontend`**. Leave build command / publish directory
   untouched — `frontend/netlify.toml` pins them (`npm run build`, `.next`) and
   enables the official Next.js runtime automatically.
3. **Site settings → Environment variables** → add:
   | Variable | Value |
   |---|---|
   | `API_ORIGIN` | `https://memecoin-radar-api.up.railway.app` ← your Railway URL, **no trailing slash** |
4. Deploy. Live at `https://<your-site>.netlify.app`. Same rule as Vercel:
   changing `API_ORIGIN` requires a **redeploy** (rewrites are baked at build).

**Vercel vs Netlify here:** functionally identical for this app — both free for
this traffic, both proxy `/api/*` at the edge. Vercel builds Next.js natively
(slightly faster cold SSR, zero adapter); Netlify is equally fine and handy if
you already use it or want its analytics/form extras. You can even deploy both
against the same Railway backend and pick one for your custom domain.

> **Why the backend can't move to Netlify too:** the API is a persistent
> Python process with a 24/7 ingestion scheduler and DB writes. Netlify hosts
> frontends and short-lived JS/Go functions only — persistent servers belong on
> Railway, Render, Fly.io, or a VPS.

## Step 3 — optional polish

- **Custom domain:** Vercel → Settings → Domains (free, automatic HTTPS).
- **Access:** visitors are logged in automatically with a **read-only** token
  (when `DEMO_MODE=1`). Anything that changes data — alerts, watchlist, paper
  trader, admin jobs — needs **Owner login** (sidebar) with `OWNER_KEY`. Set
  `DEMO_MODE=0` to hide the app from visitors entirely.
- **Notes (§31):** the app is analytics-only — keep it that way if you share it.

## Updating the site

Both platforms auto-deploy on push: `git push` → Railway rebuilds the API,
Vercel rebuilds the frontend. Schema changes are safe: startup runs
`create_all` + lightweight column migrations automatically.

## Costs (approximate, Sept 2026)

| Piece | Plan | Cost |
|---|---|---|
| Vercel *or* Netlify frontend | Hobby / Starter | $0 |
| Railway API + Postgres | Hobby (~$5 credit/mo) | typically $0–5 |
| DexScreener market data | public API | $0 (rate-limited, app throttles to ~1 req/s) |

Simulated social providers cost nothing; plugging a real X API later is the only
material operating cost (~$200/mo) — and it's optional by design (mock adapter).
