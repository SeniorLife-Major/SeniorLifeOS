# SeniorLife OS — Kubernetes & ArgoCD GitOps Deployment

This directory contains individual, declarative Kubernetes manifests and ArgoCD configurations for deploying SeniorLife OS via GitOps.

---

## Architecture Overview

```
                      +-----------------------------+
                      |     GitHub CI Pipeline      |
                      |  (Build, Test, GHCR Push)   |
                      +--------------+--------------+
                                     |
                    Updates tag in k8s manifests
                    Commits: deployed:<version> [skip ci]
                                     |
                                     v
                      +-----------------------------+
                      |   Git Repository (main)     |
                      |          k8s/               |
                      +--------------+--------------+
                                     |
                             ArgoCD watches k8s/
                                     |
                                     v
                      +-----------------------------+
                      |       ArgoCD Server         |
                      |  (Auto-sync & Self-heal)    |
                      +--------------+--------------+
                                     |
                        Applies desired state
                                     |
                                     v
            +-------------------------------------------------+
            |              Kubernetes Cluster                 |
            |            Namespace: seniorlifeos              |
            |                                                 |
            |  +--------------------+   +------------------+  |
            |  |    Ingress         |   | Document Vault   |  |
            |  | (HTTP / Port 8000) |   | PVC (10Gi)       |  |
            |  +---------+----------+   +--------+---------+  |
            |            |                       |            |
            |            v                       v            |
            |  +-------------------------------------------+  |
            |  | Deployment: seniorlife-backend            |  |
            |  |   - Init: wait-for-postgres               |  |
            |  |   - Init: Alembic database migration      |  |
            |  |   - Container: FastAPI + LangGraph        |  |
            |  +--------------------+----------------------+  |
            |                       |                         |
            |                       v                         |
            |  +-------------------------------------------+  |
            |  | Service & Deployment: seniorlife-postgres |  |
            |  | Storage: postgres-data-pvc (10Gi)         |  |
            |  +-------------------------------------------+  |
            +-------------------------------------------------+
```

---

## Directory Manifest Structure

Every resource is maintained in its own dedicated YAML file:

| File | Resource Kind | Description |
|---|---|---|
| [`namespace.yaml`](./namespace.yaml) | `Namespace` | Defines the isolated `seniorlifeos` namespace |
| [`configmap.yaml`](./configmap.yaml) | `ConfigMap` | Non-sensitive runtime application configuration |
| [`secret.yaml`](./secret.yaml) | `Secret` | Database password, JWT secrets, and AI provider API keys |
| [`postgres-pvc.yaml`](./postgres-pvc.yaml) | `PersistentVolumeClaim` | 10Gi persistent volume claim for PostgreSQL data directory |
| [`postgres-deployment.yaml`](./postgres-deployment.yaml) | `Deployment` | PostgreSQL 16 database with health check probes |
| [`postgres-service.yaml`](./postgres-service.yaml) | `Service` | Internal ClusterIP service exposing PostgreSQL on port 5432 |
| [`backend-pvc.yaml`](./backend-pvc.yaml) | `PersistentVolumeClaim` | 10Gi persistent storage for the encrypted document vault (`/app/var/documents`) |
| [`backend-deployment.yaml`](./backend-deployment.yaml) | `Deployment` | SeniorLife OS backend with migration initContainers and liveness/readiness probes |
| [`backend-service.yaml`](./backend-service.yaml) | `Service` | ClusterIP service exposing SeniorLife OS HTTP API on port 8000 |
| [`ingress.yaml`](./ingress.yaml) | `Ingress` | Nginx Ingress routing traffic to `seniorlife-backend:8000` |
| [`kustomization.yaml`](./kustomization.yaml) | `Kustomization` | Kustomize bundle combining all manifests and image overrides |
| [`argocd-application.yaml`](./argocd-application.yaml) | `Application` | ArgoCD application definition with automated sync and self-healing |
| [`argocd-cm.yaml`](./argocd-cm.yaml) | `ConfigMap` | ArgoCD configuration setting `timeout.reconciliation: "10s"` for fast polling |
| [`argocd-repo-secret.yaml`](./argocd-repo-secret.yaml) | `Secret` | ArgoCD GitHub credentials template to prevent API rate limits |

---

## Automated CI/CD & ArgoCD Workflow

1. **Build & Test**: When a push occurs on `main`, GitHub Actions builds the image and runs the test suite.
2. **Tag & Publish**: CI increments the semantic version tag (e.g. `v1.1.1`) and publishes the image to `ghcr.io/seniorlife-major/seniorlifeos:<version>`.
3. **Manifest Update & Push**: `ci/deploy.py` updates the image tag in [`backend-deployment.yaml`](./backend-deployment.yaml) and [`kustomization.yaml`](./kustomization.yaml), and commits the change:
   ```bash
   git commit -m "deployed:<version> [skip ci]"
   git push origin main
   ```
4. **CI Loop Prevention**:
   - The commit message includes `[skip ci]`, causing GitHub Actions to skip triggering any workflows.
   - The workflow configuration uses `paths-ignore: ['k8s/**', 'docs/**', '**.md']`, completely ignoring pushes touching only manifests.
   - The workflow condition `if: "!startsWith(github.event.head_commit.message, 'deployed:')"` provides a third failsafe.
5. **GitOps Sync**: ArgoCD detects the new commit in `k8s/`, reconciles the state, runs database migrations via the initContainer, and performs a zero-downtime rolling update.

---

## Quick Start & Verification

### 1. Register with ArgoCD
Apply the ArgoCD Application manifest into your ArgoCD namespace:
```bash
kubectl apply -f k8s/argocd-application.yaml -n argocd
```

### 2. Manual Dry-Run / Client-Side Diff
You can preview the generated manifests locally at any time:
```bash
kubectl kustomize k8s/
# or
kubectl apply --dry-run=client -k k8s/
```

### 3. Check Application Status
```bash
kubectl get all -n seniorlifeos
kubectl logs -n seniorlifeos -l app=seniorlife-backend -c backend -f
```
