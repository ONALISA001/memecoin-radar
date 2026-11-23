# Deploying Memecoin Radar: Railway (API + Postgres) + Netlify/Vercel (frontend)

> **This is the live setup.** The API runs on a **paid Railway plan** because it must never
> sleep: the scheduler collects prices every minute, and a gap leaves holes in the history the
> track record, backtests and paper trader learn from. Render's free plan slept for 42 hours on
> Nov 21–23, 2026, which is why we moved. The old free guide is kept in
> [docs/DEPLOY_RENDER_SUPABASE.md](docs/DEPLOY_RENDER_SUPABASE.md) (`render.yaml`), but don't
> run both hosts at once: two schedulers writing to one database double-count everything.
>
> **Database: Railway Postgres** (decided Nov 23, 2026). It holds the full history (4,000+ coins,
> 28k+ score snapshots, the Strategy Lab accounts). The Supabase project only has Nov 21–23 from
> the Render period and is no longer written to.

```
browser ──► your-site.netlify.app (Next.js)
                 │  server-side rewrite /api/*   (no secrets in the browser)
                 ▼
           your-api.up.railway.app (FastAPI + 24/7 scheduler)
                 ▼
           Railway Postgres (same project; full history)
```

---

## Step 0 — put the code on GitHub (once)

Already done: `github.com/ONALISA001/memecoin-radar`.

## Step 1 — backend on Railway (~10 min)

1. <https://railway.com> → **New Project** → **Deploy from GitHub repo** → pick
   `memecoin-radar`.
2. In the service's **Settings → Source** set **Root Directory: `backend`**. Railway reads
   `backend/railway.toml` (Dockerfile build, health check, always-restart policy).
3. **+ New → Database → PostgreSQL** in the same project (skip if it exists). **Settings →
   Deploy → Region**: same region as the database. Leave **Serverless / App Sleeping OFF**.
4. **Variables** — copy them from the Render dashboard (Environment tab), then:
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (variable reference to the Railway Postgres; internal network) |
   | `OWNER_KEY` | same as on Render (your owner login) |
   | `JWT_SECRET` | same as on Render (or a new long random string: owners log in again) |
   | `HELIUS_API_KEY` | a **new** key from the Helius dashboard (the old one was printed in Render's logs; delete it there) |
   | `DEMO_MODE` | `1` |
   | `SEED_TICK` | `0` |
   | `KEEP_AWAKE` | `0` (a paid Railway service never sleeps; no self-ping needed) |
   | `DB_POOL_SIZE` | `5` |
   | `JWT_TTL_HOURS` | `168` |
   | `LLM_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | copy if you set them on Render |
5. **Settings → Networking → Generate Domain** → copy the URL, e.g.
   `https://memecoin-radar-api.up.railway.app`.
6. Wait for the deploy to go green, then open `https://<your-domain>/health`. Within ~2
   minutes it should show `"stale": false` and a small `last_score_age_s`.

### Then switch over
7. **Netlify → Site settings → Environment variables**: set `API_ORIGIN` to the Railway URL
   (no trailing slash) and **redeploy** the site (rewrites are baked in at build time).
8. **Render dashboard → memecoin-radar-api → Settings → Suspend service** (or delete it) so
   only one scheduler writes to the database.
9. GitHub → repo **Settings → Secrets and variables → Actions**: if the nightly backup workflow
   uses the Render URL anywhere, point it at Railway. Set the `DB_BACKUP_URL` secret to the
   Postgres service's `DATABASE_PUBLIC_URL` (see [docs/BACKUPS.md](docs/BACKUPS.md)).

### What keeps it running
- **Watchdog:** if no scheduler tick finishes for 20 minutes (`WATCHDOG_RESTART_MIN`), the
  app exits and Railway restarts it (`restartPolicyType = "ALWAYS"`). On by default on
  Railway/Render (`WATCHDOG=0` turns it off).
- **`/health` → `"stale": true`** when the newest score is older than 15 minutes
  (`STALE_AFTER_MIN`). Point any uptime monitor (e.g. UptimeRobot keyword check for
  `"stale":false`) at it to get an alert when data stops.
- **DexScreener pacing:** every DexScreener call in the process shares one pace (0.4 s apart,
  `DEX_MIN_INTERVAL_S`) and a 429 pauses all of them, so the loops no longer trip the limit
  together.
- **No API keys in logs:** request URLs (Helius puts its key in the URL) are no longer logged.

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
| Railway API (always on) | Hobby/Pro | your Railway plan |
| Railway Postgres | included in the plan | usage-based |
| DexScreener market data | public API | $0 (rate-limited; app paces all calls together) |

Simulated social providers cost nothing; plugging a real X API later is the only
material operating cost (~$200/mo) — and it's optional by design (mock adapter).
