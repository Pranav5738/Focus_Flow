# Deployment (end-to-end) for pranavS.store

This repo is a **React static frontend** + **FastAPI backend**.
A clean production setup is:

- Frontend: `https://pranavS.store` (and `https://www.pranavS.store`)
- Backend API: `https://api.pranavs.store`

Note: domains are case-insensitive; DNS typically uses lowercase.

Below is a complete start → finish checklist using **Namecheap DNS** + (recommended) managed hosting:

- Frontend hosting: **Vercel** (simple static hosting + custom domain)
- Backend hosting: **Render / Railway / Fly.io** (any host that can run `uvicorn`)
- Database: **MongoDB Atlas** (recommended for production)

If you prefer a single VPS deployment (Nginx reverse proxy), tell me and I’ll write that exact variant too.

---

## 0) One-time prerequisites

- You have a GitHub repo with this code pushed.
- You can log into Namecheap for `pranavS.store`.
- Decide your backend host (Render/Railway/Fly). You’ll need the **service URL** it gives you (like `your-app.onrender.com`).

---

## 1) Backend: provision database (MongoDB Atlas)

1. Create an Atlas cluster.
2. Create a DB user + password.
3. Add your backend host IP rules:
   - Easiest: allow from anywhere (`0.0.0.0/0`) during setup.
   - Better later: restrict to your host’s outbound IPs (if provided).
4. Copy your connection string and keep it for `MONGO_URL`.

---

## 2) Backend: deploy FastAPI

### 2.1 Required runtime command

Your host must run:

- `python -m uvicorn backend.server:app --host 0.0.0.0 --port $PORT`

(Hosts usually inject `PORT` automatically.)

### 2.2 Required environment variables

Set these on your backend hosting platform (NOT in git):

- `APP_ENV=production`
- `JWT_SECRET=<generate a long random secret>`
- `CORS_ORIGINS=https://pranavS.store,https://www.pranavS.store`
- `MONGO_URL=<your atlas connection string>`
- Optional: `DB_NAME=habit_tracker`

Example file: [backend/.env.example](backend/.env.example)

### 2.3 Health check

After deploy, verify:

- `https://<your-backend-host>/api/health` returns `{ "status": "healthy" }`

---

## 3) Backend: attach custom domain `api.pranavs.store`

You’ll do this in two places:

### 3.1 In your backend host dashboard

- Add a custom domain: `api.pranavs.store`
- The host will show you a DNS target (usually a **CNAME target** like `your-app.onrender.com`)

### 3.2 In Namecheap → Advanced DNS

Add a record:

- Type: `CNAME`
- Host: `api`
- Value: `<the exact target your backend host gave you>`
- TTL: Automatic

Wait for DNS to propagate (can be minutes; sometimes up to an hour).

### 3.3 SSL

Most hosts automatically issue SSL once DNS points correctly.
When ready, verify:

- `https://api.pranavs.store/api/health`

---

## 4) Frontend: deploy React

This frontend is already set up to read the backend URL from:

- `REACT_APP_BACKEND_URL`

(See [frontend/src/lib/api.js](frontend/src/lib/api.js).)

### 4.1 Set production env var

In your frontend hosting platform, set at **build time**:

- `REACT_APP_BACKEND_URL=https://api.pranavs.store`

Example file: [frontend/.env.production.example](frontend/.env.production.example)

### 4.2 Build settings

If your host asks for these:

- Build command: `npm run build`
- Output directory: `build`
- Root directory: `frontend`

---

## 5) Frontend: connect Namecheap domain `pranavS.store`

### Recommended domain mapping

- `pranavS.store` → frontend
- `www.pranavS.store` → frontend

### 5.1 In your frontend host dashboard

Add domains:

- `pranavS.store`
- `www.pranavS.store`

Your host will tell you exactly what DNS records to add.

### 5.2 Common DNS setup (Vercel example)

In Namecheap → Advanced DNS:

- **A Record**
  - Host: `@`
  - Value: `76.76.21.21`

- **CNAME Record**
  - Host: `www`
  - Value: `cname.vercel-dns.com`

If you use Cloudflare Pages/Netlify/etc, follow their values instead (they differ).

### 5.3 SSL

Your frontend host should auto-provision SSL.
Verify:

- `https://pranavS.store`
- `https://www.pranavS.store`

---

## 6) Final verification checklist

1. Frontend loads: `https://pranavS.store`
2. Backend health works: `https://api.pranavs.store/api/health`
3. Open DevTools → Network and confirm API calls go to `https://api.pranavs.store/api/...`
4. If you see CORS errors:
   - Confirm `CORS_ORIGINS` contains the exact origin(s):
     - `https://pranavS.store`
     - `https://www.pranavS.store`
   - Redeploy backend after changing env vars.

---

## 7) Notes / common issues

- If you deploy multiple backend instances, the in-process scheduler (APScheduler) may run on each instance. For a single-instance deployment, it’s fine.
- Don’t use the file-backed DB in production unless you’re on a single VM with persistent disk.
- `JWT_SECRET` must be set when `APP_ENV=production` (the backend refuses to start otherwise).
