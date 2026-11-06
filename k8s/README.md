# SeniorLife OS — Kubernetes Manifests & GitOps Deployment

This directory contains the declarative Kubernetes manifests for SeniorLife OS.

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
