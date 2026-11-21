# Free hosting: Render + Supabase (no credit card)

Moves the backend off Railway. Nothing changes for the frontend except one setting on Netlify.

| Part | Where | Cost |
|---|---|---|
| Database (Postgres) | **Supabase** free project | free: 500 MB, 2 projects |
| Backend (FastAPI + 60-second data loop) | **Render** free web service | free: 750 hours/month (one service 24/7 ≈ 720 h) |
| Frontend (Next.js) | **Netlify** (unchanged) | free |

What the code already does for this setup:
- **Keeps itself awake.** Render's free services sleep after 15 minutes without visits, which would stop
  the data loop. The backend pings its own public address every 10 minutes (`KEEP_AWAKE`).
- **Fits in 500 MB.** Score snapshots older than 2 hours are trimmed from ~11 KB to ~1 KB (only the
  summary that charts and backtests read). 72 hours of history ≈ 40 MB instead of ≈ 350 MB.
- **Starts fast.** `SEED_TICK=0` opens the web port immediately; the first data tick runs right after.
- **Supabase connection.** `sslmode=require` is added automatically; the connection pool is small.
- `GET /health` shows database size and data freshness.

Total time: about 20–30 minutes.

---

## 1. Supabase: create the database (≈ 5 min)

1. Go to **supabase.com** → *Start your project* → sign in with **GitHub**.
2. **New project**:
   - Name: `memecoin-radar`
   - **Database password**: click *Generate a password* and **save it somewhere safe** (you need it in step 2).
   - Region: pick one close to you, and remember it (Render's region should be close to it).
   - Plan: **Free**.
3. Wait until the project says it's ready (1–2 minutes).
4. Click **Connect** (top of the project page) → **Connection string** → choose **Session pooler**.
   Copy the URI. It looks like:
   ```
   postgresql://postgres.abcdefghijklmnop:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
   ```
5. Replace `[YOUR-PASSWORD]` with the password from step 2.
   - If the password contains `@ : / ? # %`, those characters must be URL-encoded
     (for example `@` → `%40`). A generated password usually avoids them.
   - Use **Session pooler** (port 5432), not *Direct connection*: on the free plan the direct
     address is IPv6-only and Render can't reach it.

You don't need to create any tables: the backend creates them on first start.

## 2. Render: deploy the backend (≈ 10 min)

1. Go to **render.com** → *Get Started* → sign in with **GitHub** and allow access to the
   `memecoin-radar` repository.
   - If Render asks for a credit card and you can't add one, stop here and see *If Render asks for a card* below.
2. Dashboard → **New** → **Blueprint** → pick the `memecoin-radar` repo (branch `main`).
   Render reads `render.yaml` from the repo and shows one service: **memecoin-radar-api** (Free).
3. Fill in the values it asks for:
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase URI from step 1.5 (with your password) |
   | `OWNER_KEY` | a long password you choose: this is what you type in **Owner login** |
   | `HELIUS_API_KEY`, `LLM_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional: paste the same values you had on Railway, or leave blank |
   `JWT_SECRET` is generated automatically. Owner logins last a week (`JWT_TTL_HOURS=168`); change it in the service's *Environment* tab if you want.
4. Click **Apply**. The first build takes about 5 minutes (*Logs* tab shows progress).
5. When it says **Live**, copy the service address at the top, e.g.
   `https://memecoin-radar-api.onrender.com`.
6. Check it in the browser: open `https://memecoin-radar-api.onrender.com/health`. You should see
   `"ok": true` and `"db": "postgresql"`. After a minute or two `last_score_age_s` becomes a small number
   (data is flowing).

The `region` in `render.yaml` is `frankfurt`. If your Supabase project is in another part of the world,
change `region:` (oregon, ohio, virginia, frankfurt, singapore) before step 2, or ignore it: it only
affects speed a little.

## 3. Netlify: point the frontend at the new backend (≈ 3 min)

1. Netlify → your site → **Site configuration** → **Environment variables**.
2. Edit `API_ORIGIN` → set it to the Render address from step 2.5, **without a trailing slash**:
   `https://memecoin-radar-api.onrender.com`
3. **Deploys** → **Trigger deploy** → **Clear cache and deploy site**
   (the address is baked in at build time, so a redeploy is required).

## 4. Log in and restart the paper trader (≈ 2 min)

1. Open your site → sidebar **Owner login** → type your `OWNER_KEY`.
2. Paper trader → **Strategy Lab** → **Start lab** (the strategy accounts are new on the new database).

Done. The data loop now runs on Render, stores into Supabase, and the site reads from it.

---

## Optional: a second keep-awake pinger

The backend already pings itself. As a backup (for example after Render restarts it), add a free
external pinger:

1. **cron-job.org** → sign up → *Create cronjob*.
2. URL: `https://memecoin-radar-api.onrender.com/` (the root address: it's cheap and doesn't touch the database).
3. Schedule: every **10 minutes**. Save.

## Optional: bring your Railway data

Only possible if Railway still lets you see the database connection string (Railway → Postgres service →
*Variables* → `DATABASE_PUBLIC_URL`). Market snapshots only cover 3 days and refill by themselves; this
matters for paper-trading history, verdict history and strategy-lab results.

Do this **before step 2** (on an empty Supabase database), with the PostgreSQL client tools installed
(`pg_dump` / `pg_restore`, from postgresql.org/download):

```bash
pg_dump "RAILWAY_DATABASE_PUBLIC_URL" --no-owner --no-privileges -Fc -f radar.dump
```

```bash
pg_restore --no-owner --no-privileges -d "SUPABASE_SESSION_POOLER_URI" radar.dump
```

---

## Limits to know

- **Supabase 500 MB.** Watch `db_size_mb` on `/health`. With snapshot trimming it should stay well under
  100 MB. If it ever climbs, lower `SNAPSHOT_SLIM_AFTER_H` (e.g. `1`) on Render.
- **Supabase pauses** a free project after a week of *no* database activity. The data loop writes every
  minute, so this only happens if the backend is down for a week.
- **Render 750 free hours/month** are shared by all free services in your Render account: keep only this one.
- Render may **restart** a free service at any time: it comes back by itself; a backtest that was running
  at that moment must be started again.
- After a long stop (e.g. Render suspended), the first request can take ~1 minute while the service wakes.

## If Render asks for a card

- Try **Northflank** (free sandbox) with the same Docker setup: build context `backend/`, Dockerfile
  `backend/Dockerfile`, port `8000`, and the same environment variables as the table in step 2
  (plus `SEED_TICK=0`, `KEEP_AWAKE=1`, and `KEEP_AWAKE_URL=<the service's public address>`).
- Or run the backend on your own PC against Supabase (works only while the PC is on): put
  `DATABASE_URL`, `OWNER_KEY` and `JWT_SECRET` in `backend/.env`, then
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`, and expose it with a free tunnel
  (for example Cloudflare Tunnel) so Netlify's `API_ORIGIN` can reach it.

## Troubleshooting

| You see | Fix |
|---|---|
| "owner login not configured (set OWNER_KEY on the server)" | Render → service → *Environment* → add `OWNER_KEY` → *Save, rebuild and deploy*. |
| Deploy fails: "DATABASE_URL is empty" | Add `DATABASE_URL` in Render → *Environment*. |
| Logs: `password authentication failed` | Wrong password in `DATABASE_URL` (or it needs URL-encoding). Supabase → *Project Settings* → *Database* → *Reset database password* if lost. |
| Logs: `Network is unreachable` / IPv6 address | You used *Direct connection*. Use the **Session pooler** URI. |
| `/health` works but the site shows no data / errors | Netlify `API_ORIGIN` still points at Railway: fix it and **Clear cache and deploy**. |
| `/health` shows `last_score_age_s` growing | Data loop stopped: check Render *Logs*; the service may have been asleep (add the cron-job.org pinger). |
