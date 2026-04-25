# CIM Analyst — Deployment Plan

## Overview

Deploy the CIM Analyst Streamlit dashboard as a web application accessible via
HTTPS with authentication. Keep the existing stack; containerize and ship.

**Stack**: Streamlit + SQLite + Docker + Railway (or VPS) + Cloudflare Tunnel  
**Cost**: ~$7-10/month  
**Timeline**: 2-3 weeks, one developer  

---

## Architecture

```
[Browser] --> [Cloudflare Tunnel + Access] --> [Docker Container]
                                                  |
                                                  +-- Streamlit (port 8501)
                                                  +-- Analysis Pipeline (Python)
                                                  +-- SQLite comp DB
                                                  +-- Persistent Volume (/data)
                                                       +-- deals/
                                                       +-- cim_comps.db
                                                       +-- backups/
```

Single container. No microservices, no Postgres, no S3, no Celery.

---

## Phase 1: Containerize (Week 1, Days 1-2)

### 1.1 Externalize filesystem paths

Three files need env var support so paths work in containers:

| File | Variable | Default (local dev) |
|------|----------|-------------------|
| `config.py` | `COMP_DB_PATH` | `data/cim_comps.db` |
| `gui/deal_manager.py` | `CIM_DEALS_DIR` | `./deals` |
| `gui/pages/upload_analyze.py` | Temp file cleanup | `tempfile.mkdtemp()` |

### 1.2 Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "gui/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.maxUploadSize=100"]
```

### 1.3 docker-compose.yml

```yaml
services:
  cim-analyst:
    build: .
    ports:
      - "8501:8501"
    volumes:
      - cim_data:/data
    environment:
      - COMP_DB_PATH=/data/cim_comps.db
      - CIM_DEALS_DIR=/data/deals
      - CENSUS_API_KEY=${CENSUS_API_KEY:-}
    restart: unless-stopped

volumes:
  cim_data:
```

### 1.4 .dockerignore

```
__pycache__/
*.pyc
.git/
deals/
data/cim_comps.db
overrides/*.json
*.pdf
logs/
.env
tmp*
```

### 1.5 Streamlit production config

`deploy/streamlit_config.toml`:
```toml
[server]
headless = true
enableCORS = false
enableXsrfProtection = true
maxUploadSize = 100

[browser]
gatherUsageStats = false
```

### 1.6 Verification

```bash
docker compose build
docker compose up
# Open http://localhost:8501
# Upload a test CIM, verify full pipeline produces memo + model + template
```

---

## Phase 2: Secure & Deploy (Week 1, Days 3-5)

### 2.1 Authentication — Cloudflare Tunnel + Access

No application code changes. Zero-trust auth at the network layer.

1. Install `cloudflared` in Dockerfile (or as sidecar)
2. Create Cloudflare Tunnel pointing to `localhost:8501`
3. Add Access policy: allow `*@your-company.com`
4. Users hit `https://cim.your-domain.com`, authenticate via email

**Alternative (simpler)**: Tailscale mesh network. Install Tailscale on the
container and on team laptops. Access via Tailscale IP. No public exposure.

### 2.2 Deploy to Railway

1. Push repo to GitHub (private)
2. Create Railway project, connect to repo
3. Add persistent volume mounted at `/data` (1GB, $0.25/mo)
4. Set environment variables: `COMP_DB_PATH`, `CIM_DEALS_DIR`, `CENSUS_API_KEY`
5. Deploy

Seed comp DB: copy local `data/cim_comps.db` to Railway volume via `railway exec`.

### 2.3 Verification

- [ ] App accessible at Railway URL / custom domain
- [ ] Auth required (unauthenticated users see nothing)
- [ ] Upload CIM, full pipeline completes
- [ ] Download memo (.docx), model (.xlsx), template (.xlsm)
- [ ] Deal appears in Deal Tracker
- [ ] Comp saved to database
- [ ] Container restart preserves deals and comp DB

---

## Phase 3: Harden (Week 2)

### 3.1 SQLite WAL mode

Add to `data/comp_db.py` in `_init_db()`:
```python
self.conn.execute("PRAGMA journal_mode=WAL")
```
Allows concurrent reads during writes.

### 3.2 Pin dependency versions

Generate `requirements.lock` from working environment:
```bash
pip freeze > requirements.lock
```
Dockerfile uses `requirements.lock` in production.

### 3.3 Temp file cleanup

Ensure uploaded PDFs are cleaned up after analysis in
`gui/pages/upload_analyze.py`. Use `tempfile.mkdtemp()` and delete on completion.

### 3.4 Database backups

`scripts/backup_db.sh`:
```bash
#!/bin/bash
cp /data/cim_comps.db /data/backups/cim_comps_$(date +%Y%m%d).db
find /data/backups -name "*.db" -mtime +30 -delete
```
Run weekly via cron or Railway scheduled job.

### 3.5 Health check

Add `HEALTHCHECK` to Dockerfile:
```dockerfile
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD curl -f http://localhost:8501/_stcore/health || exit 1
```

---

## Phase 4: CI/CD (Week 2-3)

### 4.1 GitHub Actions

`.github/workflows/test.yml`:
```yaml
name: Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v
```

### 4.2 Pre-push audit

Before every push, run the security reviewer agent to check for:
- Leaked secrets, API keys, credentials
- Hardcoded passwords or tokens
- PII exposure
- Insecure file handling
- OWASP top 10 in any web-facing code

---

## What NOT to Build

- React/Next.js frontend (Streamlit is fine for internal tools)
- PostgreSQL (SQLite correct at this scale)
- REST API layer (no second consumer)
- Role-based access control (1-2 users, same firm)
- Celery/Redis background workers (analysis takes 10-30s)
- Multi-tenancy (one firm, shared pipeline)
- S3 file storage (persistent volume is simpler)

---

## Future Triggers

| Trigger | Action |
|---------|--------|
| 10+ concurrent users | Add background worker for batch jobs |
| Need mobile access | Consider React frontend rewrite |
| Multiple firms / tenants | PostgreSQL + proper auth + RLS |
| 50,000+ comps in DB | Still fine with SQLite, but benchmark |

---

## Cost Summary

| Item | Monthly |
|------|---------|
| Railway Pro | $5 |
| Persistent volume (2GB) | $0.50 |
| Compute (sleep when idle) | $2-5 |
| Cloudflare | Free |
| **Total** | **$7-10** |
