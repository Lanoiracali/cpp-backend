# CPP Backend Service

Python/Flask API for the CPP application. Uses **Neon Postgres** when `DATABASE_URL` is set; falls back to local SQLite otherwise.

## Requirements

- Python 3.12+
- Neon Postgres (production) or SQLite (local only)

## Local setup

1. Create and activate a virtual environment:
   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # macOS/Linux
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment:
   ```bash
   copy .env.example .env         # Windows
   cp .env.example .env           # macOS/Linux
   ```
   Set `DATABASE_URL` to your Neon connection string from the [Neon console](https://console.neon.tech).

4. Verify Neon connectivity:
   ```bash
   python check_db.py
   ```
   You should see `Postgres mode: True` and `OK: Database is reachable`.

5. Run locally (development):
   ```bash
   python main.py
   ```
   Server: `http://127.0.0.1:5000` — health check: `GET /api/v1/health`

## Database migrations

If Neon is empty or missing Part 2 tables:

```bash
python migrate_sqlite_to_pg.py --sqlite-file cppstudrecord_db.sqlite
python migrate_part2_pg.py
```

## Deploy to Railway

### Option A — GitHub (recommended)

1. Push this repo to GitHub (`Lanoiracali/cpp-backend`). Ensure `.env` is **not** committed.
2. Go to [railway.com/new](https://railway.com/new) → **Deploy from GitHub repo** → select `cpp-backend`.
3. Railway auto-detects Python via Railpack and installs from `requirements.txt`.
4. **Variables** tab → add:
   - `DATABASE_URL` = your Neon connection string (from [Neon console](https://console.neon.tech))
5. **Settings** → **Networking** → **Generate Domain** for a public HTTPS URL.
6. Deploy uses `railway.json` / `Procfile` start command:
   `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 main:app`
7. Verify:
   ```bash
   curl https://YOUR-SERVICE.up.railway.app/api/v1/health
   ```
   Expect: `{"success": true, "status": "ok", "database": "pg"}`

**Tip:** Pick a region close to Neon (`ap-southeast-1`) under service settings if available.

### Option B — Railway CLI

```bash
npm i -g @railway/cli
railway login
cd cpp-backend
railway init
railway variables set DATABASE_URL="postgresql://..."
railway up
railway domain
```

## Environment variables

| Variable        | Required | Description                          |
|-----------------|----------|--------------------------------------|
| `DATABASE_URL`  | Yes (prod) | Neon `postgresql://...` connection |
| `PORT`          | Auto     | Injected by Railway                  |

## Production notes

- Do not use `python main.py` in production; use **gunicorn** (see `Procfile` / `railway.json`).
- Point the Node gateway at this service with `FLASK_BACKEND_URL=https://your-service.up.railway.app`.
