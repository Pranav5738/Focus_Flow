# Focus_Flow
FocusFlow is a full-stack habit tracker with a React + Tailwind frontend and a FastAPI backend featuring JWT authentication, habit management, streak tracking, and analytics dashboards.

## Local development

### Backend
- Install deps: `python -m pip install -r backend/requirements.txt`
- Run: `python -m uvicorn backend.server:app --host 127.0.0.1 --port 8000 --reload`

Backend defaults to a **file-backed local DB** at `backend/data/db.json` if `MONGO_URL` is not set.

### Frontend
- Install deps: `cd frontend && npm install`
- Run: `cd frontend && npm start`

Set `REACT_APP_BACKEND_URL` (optional) if your backend is not on `http://localhost:8000`.

## Deployment

### Backend (FastAPI)

Run in production with:

`uvicorn backend.server:app --host 0.0.0.0 --port $PORT`

Required environment variables:
- `JWT_SECRET` (required in production; backend refuses to start without it)
- `CORS_ORIGINS` (comma-separated frontend origins, e.g. `https://your-frontend.com`)

Leaderboard environment variables:
- `LEADERBOARD_TZ` (optional; fixed timezone for leaderboard windows and countdowns, default `UTC`)
- `LEADERBOARD_INTERNAL_TOKEN` (optional; required only if you plan to call `POST /api/leaderboard/updateScore` for internal/admin operations)

Persistence options:
- **Recommended (production): MongoDB**
	- Set `MONGO_URL` and optionally `DB_NAME`
- **Fallback (local/demo): file-backed DB**
	- Uses `backend/data/db.json` by default
	- Override with `DATA_FILE=/path/to/db.json`
	- Not recommended for multi-instance deployments

Notes:
- If you set `CORS_ORIGINS=*`, the backend disables credentials for safety.
- The weekly leaderboard reset uses an in-process scheduler (APScheduler). For multi-instance production deployments, ensure only one instance runs the scheduler or use an external cron to trigger the reset logic (the code is idempotent).

### Frontend (React)

Build and deploy the static bundle:

`cd frontend && npm run build`

Set this environment variable at build time:
- `REACT_APP_BACKEND_URL=https://your-backend-domain.com`

See examples:
- [backend/.env.example](backend/.env.example)
- [frontend/.env.production.example](frontend/.env.production.example)
