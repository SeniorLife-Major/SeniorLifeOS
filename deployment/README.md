# SeniorLife OS — Server Deployment Guide (with Nginx HTTPS Proxy)

This folder contains the standalone **Docker Compose** deployment configuration for deploying **SeniorLife OS** directly to a production server with an **Nginx HTTPS Reverse Proxy** handling ports 80 and 443.

All environment variables, service dependencies, container images, and volumes are **set directly** in `docker-compose.yml`. No `.env` file or variable expansion is required.

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
├── docker-compose.yml        # Self-contained compose manifest (images, values & volumes)
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

### Step 2: Configure Production Values in `docker-compose.yml`

Open `docker-compose.yml` and directly set your production values under `backend.environment:`:

```bash
nano docker-compose.yml
```

Key values to configure directly in `docker-compose.yml`:
- **`GROQ_API_KEY`** or **`OPENAI_API_KEY`**: Your LLM provider API key.
- **`JWT_SECRET`**: A random 32+ character string.
- **`SENIORLIFE_HASH_KEY`**: A random 32+ character salt key for Aadhaar/PAN hashing.
- **`SENIORLIFE_STORAGE_KEY`**: A 64-hex character (32 bytes) AES encryption key for stored files:
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
  *(Never change this key once documents have been uploaded, or stored files cannot be decrypted!)*
- **`SENIORLIFE_CHECKPOINT_KEY`**: Another 64-hex character key for LangGraph paused workflow state.
- **`POSTGRES_PASSWORD`**: Strong password for PostgreSQL (update both `db.environment.POSTGRES_PASSWORD` and `backend.environment.DATABASE_URL`).

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

Interactive Swagger documentation:
- Open `https://<SERVER_IP>/docs` in your browser.

Spring Boot Actuator health endpoint:
- Open `https://<SERVER_IP>/actuator/health`

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
