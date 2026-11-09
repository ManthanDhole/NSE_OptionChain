# NSE Option Chain Helm chart

Deploys the PostgreSQL-backed application as separate Kubernetes workloads:

```text
Browser → kubectl port-forward → UI Service → UI Deployment
                                           ↓ internal HTTP
                                       API Service → API Deployment
                                                        ↓ SQL
NSE / TradingView ← Collector Deployment ─────────→ PostgreSQL StatefulSet
                         ↓                             ↓
                    Spool PVC                      Database PVC
```

The migration Job waits for PostgreSQL, creates the schema and service roles in a
transaction, and records a release-specific marker. API/collector init containers
wait for that exact marker before starting. A shared runtime ConfigMap supplies
a fresh token even when reinstalling revision 1 against a retained database. Each Helm upgrade runs a new migration
Job and rolls the backend pods through the same startup gate.

## Prerequisites

- Kubernetes **1.28+**, Helm **3.12+ or 4**, `kubectl`, and Docker/build tooling.
- A default StorageClass, or set both `postgresql.storageClass` and
  `collector.spool.storageClass` to suitable provisioners.
- The cluster must be able to pull the configured images. For private registries,
  set `imagePullSecrets` to existing registry Secret names.
- Nodes must allow outbound HTTPS/DNS for NSE/TradingView. Collection has no time or day gate: the collector runs continuously with a 5-second interval.

Helm 4.0 can report `metadata.managedFields must be nil` when re-adopting retained
resources after uninstall. On Helm 4, use **direct `helm install`** with client-side
apply for a reinstall; `helm upgrade --install` in Helm 4.0 does not reliably pass
this option through to its install path:

```sh
helm install nifty ./helm/nse-option-chain -n upnifty \
  --server-side=false --wait --wait-for-jobs --timeout 15m
```

Include your original values/image overrides. If a reinstall already left a
failed release, recover it with `helm upgrade nifty ./helm/nse-option-chain -n
nifty --reuse-values --server-side=false --wait --wait-for-jobs --timeout 15m`.
Omit `--server-side` with Helm 3. This changes Helm's apply method, not TLS or
cluster permissions.

The bundled database is a **single PostgreSQL 18 instance**, suitable for local
clusters and small installations. For production availability, backups, or managed
storage, use an external PostgreSQL service or a database operator instead.
The Helm release does not import data from your native or Compose database.

## 1. Build the two application images

Run from the repository root:

```sh
docker build --target backend -t nifty-backend:local .
docker build --target ui -t nifty-ui:local .
```

Both images are required. PostgreSQL uses the official `postgres:18` image.
The chart's default image pull policy is `IfNotPresent`.

### Local kind cluster

Use an existing local kind cluster, or create a new one:

```sh
kind create cluster --name nifty
kind load docker-image nifty-backend:local nifty-ui:local --name nifty
```

### Local minikube cluster

```sh
minikube start
minikube image load nifty-backend:local
minikube image load nifty-ui:local
```

### Other / remote clusters

Push versioned images to a registry your cluster can access:

```sh
docker tag nifty-backend:local YOUR_REGISTRY/nifty-backend:1.0.0
docker tag nifty-ui:local YOUR_REGISTRY/nifty-ui:1.0.0
docker push YOUR_REGISTRY/nifty-backend:1.0.0
docker push YOUR_REGISTRY/nifty-ui:1.0.0
```

For nodes on a different CPU architecture, build/push matching images or a
multi-architecture image with `docker buildx build --platform linux/amd64,linux/arm64`.
Specify the appropriate `--target`, tag, and `--push` for each image.

## 2. Install with Helm

Verify `kubectl config current-context` points to your intended cluster first.
For locally loaded default images:

```sh
helm upgrade --install nifty ./helm/nse-option-chain \
  --namespace nifty --create-namespace \
  --wait --wait-for-jobs --timeout 15m
```

For registry images, supply the image repositories/tags:

```sh
helm upgrade --install nifty ./helm/nse-option-chain \
  --namespace nifty --create-namespace \
  --set images.backend.repository=YOUR_REGISTRY/nifty-backend \
  --set images.backend.tag=1.0.0 \
  --set images.ui.repository=YOUR_REGISTRY/nifty-ui \
  --set images.ui.tag=1.0.0 \
  --wait --wait-for-jobs --timeout 15m
```

Store installation-specific settings in a private values file and pass `-f` rather
than repeating overrides. Use the **same settings** for upgrades.

```sh
kubectl -n nifty get pods,services,pvc,jobs
helm test nifty -n nifty --logs
```

`helm test` checks the UI Service, the UI-to-API proxy, API state, and database
connectivity. It does not require the market to be open or RSI to be available.
The collector readiness check monitors its heartbeat. Collection runs whenever
the collector is up, including nights, weekends, and holidays.

## 3. Open the UI through its Service

```sh
kubectl -n nifty port-forward service/nifty-nse-option-chain-ui 8765:80
```

Keep that terminal open and visit **http://localhost:8765**. This exposes only the
UI locally. The browser's `/api/*` requests go through the UI to the internal API
Service; no separate API/database port-forward is needed.

The Service is `ClusterIP` by default. No ingress, NodePort, or LoadBalancer is
required. All ports bind locally through kubectl unless you explicitly change
its address. If a local app is already using 8765, stop it first or change the
browser origin and local forward port together:

```sh
helm upgrade nifty ./helm/nse-option-chain -n nifty \
  --reuse-values --set ui.origin=http://localhost:8766 \
  --wait --wait-for-jobs --timeout 15m
kubectl -n nifty port-forward service/nifty-nse-option-chain-ui 8766:80
```

Open http://localhost:8766. `ui.origin` controls allowed browser-origin checks for
manual refresh POSTs. If using another release name or `fullnameOverride`, use
the exact Service name printed by `helm status nifty -n nifty`.

## Configuration

See [values.yaml](values.yaml) for the full settings.

| Setting | Default | Purpose |
| --- | --- | --- |
| `images.backend.*` | `nifty-backend:local` | API, collector, migration and wait image |
| `images.ui.*` | `nifty-ui:local` | UI-only image |
| `ui.service.type` / `.port` | `ClusterIP` / `80` | UI Service exposure |
| `ui.origin` | `http://localhost:8765` | Browser origin allowed by API |
| `ui.replicas`, `api.replicas` | `1` each | Stateless service replica counts |
| `collector.enabled` | `true` | Enable continuous collection |
| `collector.refreshSeconds` | `5` | Non-overlapping collection interval |
| `postgresql.enabled` | `true` | Use bundled database or an external one |
| `postgresql.database` | `nifty` | Application database |
| `postgresql.username` | `nifty_admin` | Migration/admin account |
| `postgresql.storage` | `20Gi` | Bundled database volume request |
| `collector.spool.storage` | `1Gi` | Durable pending-capture queue |
| `credentials.existingSecret` | empty | Use an existing credential Secret |
| `collector.caSecret` | empty | Mount a company CA bundle |
| `nodeSelector`, `tolerations`, `affinity` | empty | Pod placement |

The collector always has one replica and uses `Recreate` during upgrades. Its
SQL advisory lock prevents duplicate collectors against one database. API and UI
can scale independently. The application containers run as UID 10001 with a
read-only root filesystem; the spool and `/tmp` are writable mounts. PostgreSQL
uses UID 999 with its own persistent filesystem. No workload mounts a Kubernetes
service-account token.

### Credentials

By default the chart generates a Kubernetes Secret once, retaining and reusing it
across upgrades and reinstallations of the same release. It contains:

- `POSTGRES_PASSWORD`: admin account (PostgreSQL initialization and migration Job only).
- `COLLECTOR_PASSWORD`: `nifty_collector` role (collector only).
- `API_PASSWORD`: `nifty_api` role (API only).

The UI receives no database credentials. The backend uses libpq `PGHOST`,
`PGDATABASE`, `PGUSER`, and Secret-backed `PGPASSWORD`, so passwords with special
characters do not need URL encoding.

To supply your own credentials, create a Secret from private files without
putting passwords in command-line history, then install with
`--set credentials.existingSecret=nifty-db-credentials`:

```sh
kubectl create namespace nifty
kubectl -n nifty create secret generic nifty-db-credentials \
  --from-file=POSTGRES_PASSWORD=/private/path/admin-password \
  --from-file=COLLECTOR_PASSWORD=/private/path/collector-password \
  --from-file=API_PASSWORD=/private/path/api-password
```

Password files must contain exactly the intended password (avoid trailing
newlines). Do not change the admin Secret while reusing a populated database PVC:
PostgreSQL initialization variables do not rotate existing database passwords.
Credential rotation requires coordinated database role updates and pod restarts.

### External PostgreSQL

Provision a database and an admin account that can create tables, create/alter the
`nifty_collector` and `nifty_api` roles, and grant access. Supply its passwords using
an existing Secret and install with these values:

```yaml
postgresql:
  enabled: false
  externalHost: postgres.example.internal
  externalPort: 5432
  database: nifty
  username: nifty_admin
credentials:
  existingSecret: nifty-db-credentials
```

The chart does not create an external database. Follow your database provider's
TLS requirements. The backend uses libpq defaults; connection TLS can be configured
through the pod environment when deploying to an external TLS database.

### Company HTTPS inspection

If NSE/TradingView requests need a company CA bundle, create a Secret with an
IT-approved complete PEM bundle and point the collector at it:

```sh
kubectl -n nifty create secret generic nifty-provider-ca \
  --from-file=ca-bundle.pem=/private/path/company-ca-bundle.pem
helm upgrade nifty ./helm/nse-option-chain -n nifty --reuse-values \
  --set collector.caSecret=nifty-provider-ca \
  --wait --wait-for-jobs --timeout 15m
```

The collector mounts it read-only and sets `REQUESTS_CA_BUNDLE`. TLS verification
stays enabled. A CA certificate rejected for invalid constraints needs correction
by IT. Provider errors are reported and retried; stored data and UI/API remain
available. No holiday-calendar request is required for collection.

## Upgrades, persistence and removal

Build and distribute **new versioned tags** for code changes, then run `helm
upgrade --install` using those tags. Changing images while reusing the same tag
can leave old cached images on nodes. Migrations rerun on every release revision;
backend pods wait for their own revision's schema marker.

```sh
helm upgrade --install nifty ./helm/nse-option-chain -n nifty \
  -f /private/path/nifty-values.yaml --wait --wait-for-jobs --timeout 15m
```

Successful Helm test pods are retained so `helm test --logs` can read them; the
next test run replaces the previous test pod.

If a release is unhealthy, inspect it before uninstalling. A Helm rollback changes
application resources; it is not a database restore and cannot undo data/schema
changes. Back up PostgreSQL before upgrading application/schema versions.

```sh
helm uninstall nifty -n nifty
```

The database's StatefulSet PVC, spool PVC, and generated credential Secret are
retained. Reinstall the **same release name and namespace** with compatible values
to reuse them. Database/PVC/Secret deletion or namespace deletion can permanently
discard data or the credentials needed to access it. PostgreSQL major-version
upgrades require a separate supported data-migration process.

The chart supplies durable volumes, not automated backups or database replication.
Check `kubectl -n nifty get pvc` for volume names before setting up backups.

## Troubleshooting and validation

```sh
helm lint ./helm/nse-option-chain --strict --kube-version 1.34.0
# Render locally without connecting to a cluster (includes generated test Secrets):
helm template nifty ./helm/nse-option-chain --namespace nifty --kube-version 1.34.0
kubectl -n nifty get pods,services,pvc,jobs
kubectl -n nifty describe pod POD_NAME
kubectl -n nifty logs job/nifty-nse-option-chain-migrate-r1
kubectl -n nifty logs deployment/nifty-nse-option-chain-api
kubectl -n nifty logs deployment/nifty-nse-option-chain-collector
kubectl -n nifty logs deployment/nifty-nse-option-chain-ui
```

- `Pending` PVCs: configure an available StorageClass and enough volume capacity.
- `ImagePullBackOff`: load local images into the cluster or configure a registry
  and image-pull Secret accessible by its nodes.
- `Init` pods: inspect the matching migration Job and PostgreSQL logs/credentials.
  The Job waits for database startup, then API/collector wait for schema readiness.
- Port-forward connection failure: wait for a Ready UI pod, verify the Service
  selector/endpoints, and choose an unused local port.
- UI shows collector unavailable: inspect its pod status, logs and database
  connectivity. There are no collection time or day restrictions.
- No snapshot yet: data appears after the first successful capture;
  an empty history on a new database is expected.

To package the chart for distribution:

```sh
helm package ./helm/nse-option-chain --destination .local/helm-packages
helm install nifty .local/helm-packages/nse-option-chain-0.1.0.tgz \
  -n nifty --create-namespace --wait --wait-for-jobs --timeout 15m
```
