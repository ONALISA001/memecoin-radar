# Database backups

Every night at **02:30 UTC** a GitHub Actions workflow copies the Supabase database (all app data:
paper trades, strategy lab, verdicts, watchlist, alerts, settings), **encrypts** it, and stores it on
GitHub for **30 days**. Restoring is a button on GitHub: no software to install.

Why encrypted: the repository is public, and files stored by workflows in a public repository can
be downloaded by other GitHub users. Without your passphrase the backup file is unreadable.

## One-time setup (5 minutes)

1. GitHub → the `memecoin-radar` repository → **Settings** → **Secrets and variables** → **Actions**
   → **New repository secret**. Add two secrets:

   | Name | Value |
   |---|---|
   | `SUPABASE_DB_URL` | the same value as `DATABASE_URL` on Render (the Supabase Session-pooler address) |
   | `BACKUP_PASSPHRASE` | a long passphrase you make up (20+ characters) |

   **Save the passphrase somewhere safe** (password manager or written down). It's the only way to
   open the backups; GitHub won't show it again.
2. **Actions** tab → **Nightly database backup** → **Run workflow** → **Run workflow**.
   After ~1 minute the run turns green; its page shows *Database backup …* with the number of tables
   and an **Artifacts** section with the encrypted file.

From then on it runs by itself every night. GitHub emails you if a run fails.

If you change the Supabase password, update `SUPABASE_DB_URL` here too (and `DATABASE_URL` on Render).

## Restore a backup

This **replaces** everything in the database with the backup's contents.

1. Render → **memecoin-radar-api** → **Settings** → **Suspend Web Service** (so the backend doesn't
   write while tables are replaced).
2. GitHub → **Actions** → **Nightly database backup** → open the run you want to go back to.
   Copy the number at the end of its address: `…/actions/runs/`**`1234567890`**.
3. **Actions** → **Restore database backup** → **Run workflow**:
   - `run_id`: that number
   - `confirm`: type `RESTORE`
4. When the run is green: Render → **Resume Web Service**.

## On your own computer (optional)

Download the artifact from a backup run's page, unzip it, then (PostgreSQL 17 client tools and GnuPG
installed):

```bash
gpg --decrypt --output radar.dump radar-YYYY-MM-DD_HHMM.dump.gpg
```

```bash
pg_restore --list radar.dump
```

## Limits

- Kept **30 days** (GitHub's free storage for workflow files is limited; the database is a few MB).
- GitHub pauses scheduled workflows in a repository with **no activity for 60 days**. Any push, or
  a manual **Run workflow**, re-enables it.
- Only the `public` schema (the app's tables) is backed up; Supabase's own internal schemas are not
  needed.
