# SeniorLife OS — Server Deployment Guide (with Nginx HTTPS Proxy)

This folder contains the standalone **Docker Compose** deployment configuration for deploying **SeniorLife OS** directly to a production server with an **Nginx HTTPS Reverse Proxy** handling ports 80 and 443.

Service dependencies, container images, volumes and settings are written in `docker-compose.yml`. Secrets and API keys are not: they come from `deployment/.env` on the server (Step 2), never committed. The backend image also serves the web app, so the site and its API are one address: `https://<SERVER_IP>/`.

---

## 1. Architecture & Port Layout

Traffic arrives at Nginx, which handles SSL termination and redirects all HTTP traffic to HTTPS before forwarding requests to the FastAPI backend over the internal Docker network.

```mermaid
flowchart TD
    Client["Client / Web Browser / API Consumer"] -->|Port 80 (HTTP)| Nginx["seniorlife-nginx\n(nginx:alpine)"]
    Client -->|Port 443 (HTTPS)| Nginx

    Nginx -->|Port 80 -> 301 Redirect| Nginx
    Nginx -->|Port 443 -> Reverse Proxy (Port 8000)| Backend["seniorlife-backend\n(ghcr.io/seniorlife-major/seniorlifeos)"]

    subgraph DockerHost["Internal Network: seniorlife-net"]
        Backend -->|Port 5432 (AsyncPG/Psycopg)| DB[("seniorlife-db\n(postgres:16-alpine)")]
        Backend -.->|Port 3310 (INSTREAM, optional)| ClamAV["seniorlife-clamav\n(clamav/clamav:latest)"]
    end

    DB --> VolDB[("seniorlife_postgres_data\n(Database & Checkpoints)")]
    Backend --> VolVault[("seniorlife_document_vault_data\n(AES-256-GCM Files)")]
    Nginx -.-> VolCert[("seniorlife_certbot_www\n(ACME Challenge)")]
```

| Service | Image | Ports | Description |
|---|---|---|---|
| **`nginx`** | `nginx:alpine` | **`80:80`**, **`443:443`** | Reverse proxy, SSL/TLS termination, HTTP-to-HTTPS redirect, and ACME challenge support. |
| **`backend`** | `ghcr.io/seniorlife-major/seniorlifeos:latest` | `127.0.0.1:8000:8000` *(local loopback)* | FastAPI application running Python 3.12, PaddleOCR, LangGraph workflows, and document vault. |
| **`db`** | `postgres:16-alpine` | `5432` *(internal)* | PostgreSQL 16 database storing vault tables, audit trails, and encrypted workflow checkpoints. |
| **`clamav`** *(Optional)* | `clamav/clamav:latest` | `3310` *(internal)* | On-premise antivirus daemon for document scanning (`--profile clamav`). |

---

## 2. Directory Contents

```
deployment/
├── docker-compose.yml        # Compose manifest (images, settings & volumes)
├── .env.example              # The secrets to put in deployment/.env on the server (never committed)
├── README.md                 # This deployment guide
└── nginx/
    ├── nginx.conf            # Reverse proxy & SSL termination configuration
    └── ssl/
        ├── cert.pem          # Public SSL certificate (pre-populated fallback)
        ├── key.pem           # Private SSL key
        └── README.md         # Instructions to replace with Let's Encrypt / CA certs
```

---

## 3. Deployment Steps on the Server

### Step 1: Copy `deployment` Folder to the Server

On your server, clone the repository or copy the `deployment/` directory:

```bash
mkdir -p /opt/seniorlifeos && cd /opt/seniorlifeos

# If git is installed:
git clone https://github.com/rohanbatra/SeniorLifeOS.git .
cd deployment

# Or copy deployment/ directly via SCP / rsync from your machine:
# scp -r deployment user@server:/opt/seniorlifeos/
```

### Step 2: Put the Secrets in `deployment/.env`

Settings are written in `docker-compose.yml`; secrets and API keys never are. They live in
`deployment/.env` on the server only (git ignores it). Compose reads it by itself and **refuses to
start** while a required value is missing, so a placeholder can never go live.

```bash
cp .env.example .env
nano .env        # fill every value; .env.example says what each one is
chmod 600 .env
```

Make each random value on the server:
```bash
openssl rand -hex 32       # SENIORLIFE_STORAGE_KEY, SENIORLIFE_CHECKPOINT_KEY (64 hex characters)
openssl rand -base64 48    # JWT_SECRET, SENIORLIFE_HASH_KEY
openssl rand -hex 16       # POSTGRES_PASSWORD (letters and digits only: it goes inside DATABASE_URL)
```

- **Required:** `POSTGRES_PASSWORD`, `JWT_SECRET`, `SENIORLIFE_HASH_KEY`, `SENIORLIFE_STORAGE_KEY`,
  `SENIORLIFE_CHECKPOINT_KEY`, `GROQ_API_KEY` (doctor search always uses the AI model),
  `GEOAPIFY_API_KEY` (places typed for a doctor search).
- **Optional:** `OPENAI_API_KEY`, `GOOGLE_VISION_API_KEY`, `LANGSMITH_API_KEY`.
- **Never change `SENIORLIFE_STORAGE_KEY` or `SENIORLIFE_CHECKPOINT_KEY`** once documents are stored:
  what was stored with one key can't be opened with another. Keep a copy of `.env` somewhere safe.
- **`POSTGRES_PASSWORD` is taken only when the database volume is first created.** On a server that
  already ran with the old committed password (`postgres`), change it in the database first, then
  in `.env`, then restart the backend:
  ```bash
  docker compose exec db psql -U postgres -c "ALTER USER postgres PASSWORD '<the new one>'"
  docker compose up -d
  ```

### Step 3: Start the Containers

If pulling from a private GitHub Container Registry:
```bash
echo "$GH_PAT" | docker login ghcr.io -u <your_github_username> --password-stdin
```

Launch the stack:
```bash
docker compose up -d
```

### What Happens Automatically:
1. Docker pulls `nginx:alpine`, `postgres:16-alpine`, and `ghcr.io/seniorlife-major/seniorlifeos:latest`.
2. PostgreSQL initializes the database with volume persistence (`seniorlife_postgres_data`) and passes healthcheck (`pg_isready`).
3. Once the database is ready, `backend` starts, runs **Alembic database migrations** (`alembic -c backend/alembic.ini upgrade head`), and starts the FastAPI Uvicorn server.
4. Once the backend passes health check, `nginx` starts, serving HTTPS on port 443 with HTTP-to-HTTPS redirect on port 80.

---

## 4. SSL / HTTPS Configuration

The deployment includes a default self-signed certificate in `nginx/ssl/` so HTTPS works immediately for testing.

### Using Real Let's Encrypt / CA Certificates:

1. Copy your real certificate files into `deployment/nginx/ssl/`:
   ```bash
   cp /path/to/your/fullchain.pem deployment/nginx/ssl/cert.pem
   cp /path/to/your/privkey.pem deployment/nginx/ssl/key.pem
   ```
2. Reload Nginx without downtime:
   ```bash
   docker compose exec nginx nginx -s reload
   ```

### Obtaining Let's Encrypt Certificates with Certbot:
Because Nginx exposes `/.well-known/acme-challenge/` mapped to the `seniorlife_certbot_www` volume:
```bash
docker run -it --rm --name certbot \
  -v seniorlife_certbot_www:/var/www/certbot \
  -v /etc/letsencrypt:/etc/letsencrypt \
  certbot/certbot certonly --webroot \
  -w /var/www/certbot \
  -d yourdomain.com
```
Then copy `/etc/letsencrypt/live/yourdomain.com/fullchain.pem` to `nginx/ssl/cert.pem` and `privkey.pem` to `nginx/ssl/key.pem`.

---

## 5. Verification & Health Monitoring

Verify running containers:
```bash
docker compose ps
```

Check Nginx logs:
```bash
docker compose logs -f nginx
```

Check backend application logs:
```bash
docker compose logs -f backend
```

Verify HTTPS endpoint:
```bash
# With self-signed cert (-k to ignore verification):
curl -k https://localhost/api/v1/health

# Verify HTTP port 80 redirects to HTTPS port 443:
curl -I http://localhost/
# Output: HTTP/1.1 301 Moved Permanently
# Location: https://localhost/
```

The web app:
- Open `https://<SERVER_IP>/` in your browser (any page address, e.g. `/doctor`, opens the app).

Interactive Swagger documentation:
- Open `https://<SERVER_IP>/docs` in your browser.

Health endpoint:
- Open `https://<SERVER_IP>/api/v1/health`
- `/actuator` is never on in production (`APP_ENV=production`), even with `ACTUATOR_ENABLED=true`: it has
  no sign-in and shows environment values and request URLs. Use it on a local development server only.

---

## 6. Server Operations

### Updating to a New Release
```bash
docker compose pull
docker compose up -d
```
Migrations will run automatically during startup.

### Restarting Services
```bash
docker compose restart
```

### Accessing PostgreSQL CLI
```bash
docker compose exec db psql -U postgres -d seniorlife_os
```

### Backing Up Data Volumes
```bash
# Backup PostgreSQL database
docker compose exec db pg_dump -U postgres seniorlife_os | gzip > /backups/db_$(date +%Y%m%d).sql.gz

# Backup encrypted documents
docker run --rm \
  -v seniorlife_document_vault_data:/data:ro \
  -v /backups:/backup \
  alpine tar czf /backup/vault_$(date +%Y%m%d).tar.gz -C /data .
```

### Stopping Services
```bash
docker compose down
```
