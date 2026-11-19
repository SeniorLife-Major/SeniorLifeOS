# SeniorLife OS — Kubernetes Manifests & GitOps Deployment

This directory contains the declarative Kubernetes manifests for SeniorLife OS.

---

## Directory Manifest Structure

Every resource is maintained in its own dedicated YAML file:

| File | Resource Kind | Description |
|---|---|---|
| [`namespace.yaml`](./namespace.yaml) | `Namespace` | Defines the isolated `seniorlifeos` namespace |
| [`configmap.yaml`](./configmap.yaml) | `ConfigMap` | Non-sensitive runtime application configuration |
| [`secret.yaml`](./secret.yaml) | `Secret` | **Template only, not applied.** The keys of `seniorlife-env`, the secret you create by hand (see Secrets below) |
| [`postgres-pvc.yaml`](./postgres-pvc.yaml) | `PersistentVolumeClaim` | 10Gi persistent volume claim for PostgreSQL data directory |
| [`postgres-deployment.yaml`](./postgres-deployment.yaml) | `Deployment` | PostgreSQL 16 database with health check probes |
| [`postgres-service.yaml`](./postgres-service.yaml) | `Service` | Internal ClusterIP service exposing PostgreSQL on port 5432 |
| [`backend-pvc.yaml`](./backend-pvc.yaml) | `PersistentVolumeClaim` | 10Gi persistent storage for the encrypted document vault (`/app/var/documents`) |
| [`backend-deployment.yaml`](./backend-deployment.yaml) | `Deployment` | SeniorLife OS backend with migration initContainers and liveness/readiness probes |
| [`backend-service.yaml`](./backend-service.yaml) | `Service` | ClusterIP service exposing SeniorLife OS HTTP API on port 8000 |
| [`ingress.yaml`](./ingress.yaml) | `Ingress` | Traefik Ingress routing traffic to `seniorlife-backend:8000`, which serves the web app and the API |
| [`hpa.yaml`](./hpa.yaml) | `HorizontalPodAutoscaler` | Held at one copy for now (the vault volume is ReadWriteOnce) |
| [`kustomization.yaml`](./kustomization.yaml) | `Kustomization` | Kustomize bundle combining all manifests and image overrides |

---

## Secrets (never in git)

ArgoCD applies everything in `kustomization.yaml` and, with Prune and Self-Heal on, keeps the
cluster equal to git. So real secrets are not in git at all: the backend and postgres read
`seniorlife-env`, a secret you create by hand. `secret.yaml` only lists its keys.

**Create `seniorlife-env` before these manifests reach `main`.** Until it exists, a new backend pod
can't start (the old one keeps running) and a restarted postgres pod can't either.

1. On a machine with cluster access, write `seniorlife.env` (never commit it), one `KEY=value` per
   line, with the keys of [`secret.yaml`](./secret.yaml):
   ```bash
   openssl rand -base64 48    # JWT_SECRET, SENIORLIFE_HASH_KEY
   openssl rand -hex 32       # SENIORLIFE_STORAGE_KEY, SENIORLIFE_CHECKPOINT_KEY
   ```
   - `POSTGRES_PASSWORD` and the password inside `DATABASE_URL`: on a cluster that is already
     running, use the database's **current** password first (postgres takes `POSTGRES_PASSWORD`
     only when its volume is first created), then change it as in step 4.
   - `SENIORLIFE_STORAGE_KEY` / `SENIORLIFE_CHECKPOINT_KEY`: documents and paused searches stored
     with the old (public) keys can't be opened with new ones. Treat them as test data.
   - `GROQ_API_KEY`, `GEOAPIFY_API_KEY` and `APIFY_API`: doctor search needs all three. Every
     search asks Practo live through Apify, and a new pod has no saved doctors to fall back on.
2. Create it:
   ```bash
   kubectl -n seniorlifeos create secret generic seniorlife-env --from-env-file=seniorlife.env
   ```
3. Merge to `main`. ArgoCD rolls both deployments onto `seniorlife-env` and removes the old
   `seniorlife-secrets`, whose values were public in git.
4. Change the database password (the old one was public too):
   ```bash
   kubectl -n seniorlifeos exec deploy/seniorlife-postgres -- psql -U postgres -c "ALTER USER postgres PASSWORD '<new>'"
   # put <new> in POSTGRES_PASSWORD and DATABASE_URL in seniorlife.env, then:
   kubectl -n seniorlifeos create secret generic seniorlife-env --from-env-file=seniorlife.env      --dry-run=client -o yaml | kubectl apply -f -
   kubectl -n seniorlifeos rollout restart deploy/seniorlife-backend
   ```

To change a value later: edit `seniorlife.env`, run the two `kubectl` lines of step 4.

---

## Quick Setup: ArgoCD Web UI

1. **Install ArgoCD on your server / cluster:**
   ```bash
   kubectl create namespace argocd
   kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

   # Retrieve initial admin password:
   kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo
   ```

2. **Connect Repository in ArgoCD Web UI:**
   * Go to **Settings** → **Repositories** → **Connect Repo**.
   * Add your GitHub repository via HTTPS or SSH credentials.

3. **Create Application in ArgoCD Web UI:**
   * Click **New App**:
     * **Application Name**: `seniorlifeos`
     * **Project**: `default`
     * **Sync Policy**: `Automatic` (enable `Prune Resources` & `Self-Heal`)
     * **Repository URL**: `https://github.com/seniorlife-major/SeniorLifeOS`
     * **Revision**: `main`
     * **Path**: `k8s`
     * **Cluster URL**: `https://kubernetes.default.svc`
     * **Namespace**: `seniorlifeos` (enable `Auto-Create Namespace`)
   * Click **Create**. ArgoCD will immediately pull and reconcile the `k8s/` manifests.

---

## Local Verification / Dry-Run

You can preview the generated manifests locally at any time:
```bash
kubectl kustomize k8s/
# or
kubectl apply --dry-run=client -k k8s/
```
