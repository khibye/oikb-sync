# oikb-sync Helm chart

Self-service sync of [Open WebUI](https://github.com/open-webui/open-webui) Knowledge Bases from GitHub, GitLab, Gitea, Bitbucket, Confluence and SharePoint.

A user creates a Knowledge Base and writes the source and its credentials in the description. A small **reconciler** turns those descriptions into the configuration of the official sync tool, **[oikb](https://github.com/open-webui/oikb)**, whose daemon does all the syncing. Nobody edits YAML or redeploys anything when Knowledge Bases are added, changed or removed.

- Chart version: `0.1.0`. oikb (app) version: `0.6.0`.
- Works on OpenShift (restricted-v2 SCC) and plain Kubernetes ≥ 1.23. The platform is detected automatically.
- Hebrew guide: [`docs/he/README.md`](../../docs/he/README.md).

---

## Contents

1. [How it works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Connecting a Knowledge Base (user guide)](#connecting-a-knowledge-base-user-guide)
5. [Supported sources and settings](#supported-sources-and-settings)
6. [What is supported, what is not, and why](#what-is-supported-what-is-not-and-why)
7. [What happens when](#what-happens-when)
8. [Configuration reference (values)](#configuration-reference-values)
9. [Security](#security)
10. [Operations](#operations)
11. [GitOps (Argo CD)](#gitops-argo-cd)
12. [Troubleshooting](#troubleshooting)
13. [Upgrading and uninstalling](#upgrading-and-uninstalling)

---

## How it works

```mermaid
flowchart LR
    U[User] -->|creates / edits KB<br/>source + credentials<br/>in the description| KB

    subgraph OWUI[Open WebUI]
        KB[Knowledge Base]
        API[Sync API]
    end

    subgraph NS[Release namespace]
        R[Reconciler<br/>CronJob, every 5 min<br/>+ after helm install/upgrade]
        S[(Secret &lt;release&gt;-config<br/>.oikb.yaml)]
        D[oikb daemon<br/>Deployment, 0 or 1 replica]
    end

    SRC[GitHub / GitLab / Gitea /<br/>Bitbucket / Confluence / SharePoint]

    KB -->|list KBs| R
    R -->|writes| S
    R -.->|restart / scale 0-1| D
    S -->|mounted| D
    SRC -->|fetch, per-KB credentials| D
    D -->|diff + upload changes| API
```

**Who does what**

| Component | Does | Does not |
|---|---|---|
| **User** | Creates the Knowledge Base, writes the `oikb` lines in its description, shares it with the service user | Touch Kubernetes |
| **Reconciler** | Every 5 minutes (and right after `helm install`/`upgrade`): lists Knowledge Bases, builds `.oikb.yaml` from their descriptions, writes it to a Secret, restarts the daemon when the config changed | Sync, contact any source, create or delete Knowledge Bases |
| **oikb daemon** | Syncs each Knowledge Base from its source on its interval: scan → diff → delete stale files → upload new and changed files | Discover Knowledge Bases or read descriptions |
| **Open WebUI** | Stores files, computes the diff for oikb, indexes files for RAG, enforces access control | Decide what to sync |
| **Platform team** | Installs the chart, provides the service user's API key, network/proxy | Configure individual Knowledge Bases |

**Sync is incremental.** oikb sends the source's file list with checksums to Open WebUI (`/sync/diff`), which compares it with the checksum stored when each file was uploaded. Only new and changed files are uploaded; files no longer in the source are deleted. An unchanged source re-syncs almost for free, so restarts and frequent intervals cost nothing.

**Sync is one-way.** The source is the source of truth. A file edited inside Open WebUI keeps its upload-time checksum, so oikb never notices the edit and never restores it; files added by hand to a synced Knowledge Base are deleted on the next sync.

---

## Prerequisites

| Requirement | Why |
|---|---|
| **Open WebUI 0.9.6 or later** | oikb uses the incremental sync endpoints added in 0.9.6 |
| **Helm 3.8 or later** | `lookup`, values schema validation |
| **A service user in Open WebUI** and an **API key** for it | Both the reconciler and the daemon act as this user. They see and write only the Knowledge Bases shared with it. A regular (non-admin) user is recommended; see [Security](#security) and the steps below |
| **Network** from the release namespace to Open WebUI and to the sources | If the cluster needs an HTTP proxy, set `proxy.*`. OpenShift does not inject its cluster-wide proxy into application pods |
| **Images**: `ghcr.io/open-webui/oikb:0.6.0`, `docker.io/library/python:3.12-alpine` | Override `daemon.image` / `reconciler.image` for a mirror |

### Prepare Open WebUI (once)

API keys are **off by default** in Open WebUI, and regular users are not allowed to create them by default. An Open WebUI admin:

1. **Enables API keys:** Admin Settings → General → *Enable API Keys* (or `ENABLE_API_KEYS=true` on the Open WebUI deployment).
2. **Creates the service user**, e.g. `oikb-sync@example.com`, as a regular user (role *user*).
3. **Allows it to use API keys:** put it in a group, e.g. `oikb-sync`, with the *API Keys* feature permission (Admin → Users → Groups → the group → Permissions → Features). Alternatively turn it on in the default user permissions (`USER_PERMISSIONS_FEATURES_API_KEYS=true`) for everyone.
4. **If API key endpoint restrictions are on** (`ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS`), adds `/api/v1/knowledge,/api/v1/files` to the allowed endpoints (`API_KEYS_ALLOWED_ENDPOINTS`).
5. **Signs in as the service user** and creates the key: Settings → Account → API keys. This is `openWebUI.apiKey`.

Users then share their Knowledge Bases with this user (or with the `oikb-sync` group), with **write** permission.

---

## Installation

### 1. Minimal

```bash
helm install oikb-sync ./charts/oikb-sync \
  --namespace oikb --create-namespace \
  --set openWebUI.url=http://open-webui.open-webui.svc.cluster.local:8080 \
  --set openWebUI.apiKey=sk-...
```

Or with a values file (recommended, keep it out of Git if it contains the key):

```yaml
# my-values.yaml
openWebUI:
  url: http://open-webui.open-webui.svc.cluster.local:8080
  apiKey: sk-...
```

```bash
helm install oikb-sync ./charts/oikb-sync -n oikb --create-namespace -f my-values.yaml
```

`openWebUI.url` is the Open WebUI **base URL** as reachable from the `oikb` namespace. For an in-cluster Service in another namespace use the full name: `http://<service>.<namespace>.svc.cluster.local:<port>`.

Right after install, a one-off reconciler Job runs. If it fails (wrong URL, wrong key, no network), `helm install` fails and the reason is in its log:

```bash
kubectl -n oikb logs job/oikb-sync-reconciler-init
```

### 2. With an existing Secret (no keys in Helm values)

```bash
kubectl -n oikb create secret generic oikb-sync-keys-manual \
  --from-literal=open-webui-api-key=sk-... \
  --from-literal=oikb-api-key="$(openssl rand -hex 32)"
```

```yaml
openWebUI:
  url: http://open-webui.open-webui.svc.cluster.local:8080
existingSecret: oikb-sync-keys-manual
```

Key names can be changed with `secretKeys.*`.

### 3. Behind an HTTP proxy

```bash
oc get proxy cluster -o jsonpath='{.status.httpsProxy}{"\n"}{.status.noProxy}{"\n"}'
```

```yaml
proxy:
  httpProxy: http://proxy.example.com:3128
  httpsProxy: http://proxy.example.com:3128
  noProxy: .corp.example.com,10.0.0.0/8   # optional extras
```

The chart always adds the Open WebUI host, `.svc`, `.cluster.local`, `localhost` and `127.0.0.1` to `NO_PROXY`, so traffic to Open WebUI never goes through the proxy. The reconciler's calls to the Kubernetes API always bypass the proxy.

### 4. Verify

```bash
kubectl -n oikb get deploy,cronjob,secret -l app.kubernetes.io/instance=oikb-sync
```

The daemon Deployment shows **0 replicas until a Knowledge Base is connected**. That is expected: oikb cannot start with an empty configuration, so the reconciler scales it to 1 only when there is something to sync.

---

## Connecting a Knowledge Base (user guide)

1. **Create the Knowledge Base** in Open WebUI. Its **name (title) is free**: the reconciler identifies Knowledge Bases by ID and ignores the name.
2. **Write the `oikb` lines in the description.** Any other text in the description is ignored.

   ```
   Operations runbooks of the platform team.

   oikb: gitlab:platform/ops/runbooks
   oikb-branch: main
   oikb-path: docs/
   oikb-base-url: https://gitlab.example.com
   oikb-token: glpat-xxxxxxxxxxxxxxxxxxxx
   oikb-interval: 1h
   ```

3. **Share the Knowledge Base with the service user, with write permission.** Without sharing, the reconciler does not see it; with read-only access it is skipped with a warning.
4. **Wait** up to 5 minutes (the reconciler schedule). The daemon restarts and syncs immediately. Files then appear in the Knowledge Base; Open WebUI indexes them in the background, so they may take a moment to become searchable.

**Description rules**

- Format: `oikb-<field>: <value>`. `oikb: <source>` is the only required line.
- Keys are case-insensitive. Dashes become underscores (`oikb-base-url` → `base_url`). If a key appears twice, the first wins.
- One pair per line is easiest to read, but several pairs on one line also work. This matters: Open WebUI's Knowledge Base **edit** page shows the description in a single-line field that drops line breaks, so after an edit there all pairs end up on one line. The reconciler handles that.
- Unknown `oikb-...` lines are ignored with a warning in the reconciler log.

**Common fields (all sources)**

| Line | Required | Meaning | Default |
|---|---|---|---|
| `oikb:` | yes | The oikb source string (see below) | – |
| `oikb-interval:` | no | How often to sync: `30m`, `1h`, `6h`, `1d`, or a cron expression such as `0 6 * * 1-5` | `reconciler.defaultInterval` (`1h`) |
| `oikb-branch:` | no | Branch (git sources) | the repository's default branch |
| `oikb-path:` | no | Sync only this folder (git sources, SharePoint). Paths in the Knowledge Base become relative to it | whole repository / library |

---

## Supported sources and settings

Only connectors to which oikb passes **per-source credentials** are supported (see [why](#what-is-supported-what-is-not-and-why)).

### GitHub

| | |
|---|---|
| Source | `github:owner/repo`, `github:owner/repo/sub/folder` |
| Credentials | `oikb-token`: a **fine-grained personal access token** with **Contents: Read-only** on that repository (or a classic token with `repo`). Not needed for public repositories |
| Notes | Without a token GitHub allows **60 API requests per hour per IP**, shared by the whole cluster's egress IP. oikb makes about one request per downloaded file, so the first sync of a public repository with more than ~50 files fails with a rate-limit error. Add a token for anything but small repositories |

```
oikb: github:my-org/handbook
oikb-branch: main
oikb-token: github_pat_xxx
```

### GitLab (gitlab.com and self-managed)

| | |
|---|---|
| Source | `gitlab:group/project`, `gitlab:group/subgroup/project`, `gitlab:group/project:path/to/dir`, wiki: `gitlab:group/project?wiki=true` |
| Credentials | `oikb-token`: a **project access token** (role Reporter) or personal token with scope **`read_api`**. `read_repository` alone is not enough: oikb uses the REST API. Not needed for public projects |
| `oikb-base-url` | Self-managed only: the instance root, **without** `/api/v4`. Default `https://gitlab.com` |

```
oikb: gitlab:platform/ops/runbooks
oikb-base-url: https://gitlab.example.com
oikb-token: glpat-xxx
```

### Gitea

| | |
|---|---|
| Source | `gitea:owner/repo`, `gitea:owner/repo/sub/folder`, wiki: `gitea:owner/repo?wiki=true` |
| Credentials | `oikb-token`: an access token with `read:repository`. Not needed for public repositories |
| `oikb-base-url` | **Always required** (Gitea has no default instance): the instance root, without `/api/v1` |

```
oikb: gitea:engineering/docs
oikb-base-url: https://git.example.com
oikb-token: xxx
```

### Bitbucket Cloud

| | |
|---|---|
| Source | `bitbucket:workspace/repo`, `bitbucket:workspace/repo/sub/folder` |
| Credentials | `oikb-user` + `oikb-app-password`, both or neither. Atlassian disabled app passwords on June 9, 2026; use an **API token** instead: `oikb-user` = your **Atlassian account email**, `oikb-app-password` = the **API token** (scope: read repositories). Not needed for public repositories |
| Notes | Bitbucket Cloud only (`api.bitbucket.org`); Bitbucket Server / Data Center is not supported by oikb. Anonymous access has a low hourly rate limit |

```
oikb: bitbucket:acme/docs
oikb-user: bot@acme.com
oikb-app-password: ATATT3xxx
```

### Confluence (Cloud and Data Center)

| | |
|---|---|
| Source | `confluence:SPACEKEY`, `confluence:SPACEKEY?structure=hierarchical` (keep the page tree as folders), or with the URL in the source: `confluence:https://acme.atlassian.net/wiki/ENG` |
| Credentials (Cloud) | `oikb-base-url: https://<site>.atlassian.net/wiki`, `oikb-user: <email>`, `oikb-token: <Atlassian API token>` |
| Credentials (Data Center / Server) | `oikb-base-url: https://confluence.example.com[/context]`, `oikb-token: <personal access token>` (no `oikb-user`: Bearer auth), `oikb-api-version: v1` |
| Required | `oikb-token`, and `oikb-base-url` unless the URL is in the source |

```
oikb: confluence:ENG
oikb-base-url: https://acme.atlassian.net/wiki
oikb-user: bot@acme.com
oikb-token: ATATT3xxx
oikb-interval: 0 6 * * 1-5
```

### SharePoint Online

| | |
|---|---|
| Source | `sharepoint:<host>/<library>`, `sharepoint:<host>/sites/<site>/<library>`, `sharepoint:<host>/sites/<site>/<subsite>::<library>` |
| Credentials | All three required: `oikb-tenant-id`, `oikb-client-id`, `oikb-client-secret` of an Entra ID app registration with the Microsoft Graph **application** permission `Sites.Read.All` (admin consent), or `Sites.Selected` plus a grant on the site |
| `oikb-cloud` | `commercial` (default), `gcc_high`, `dod` |
| Not supported | Certificate authentication (needs a file; the reconciler only passes text values) |

```
oikb: sharepoint:contoso.sharepoint.com/sites/Legal/Documents
oikb-tenant-id: 00000000-0000-0000-0000-000000000000
oikb-client-id: 11111111-1111-1111-1111-111111111111
oikb-client-secret: xxx
```

### Credential lines per source (summary)

| Source | Credential lines | Without credentials |
|---|---|---|
| `github:` | `oikb-token` | ✅ public repos (rate-limited) |
| `gitlab:` | `oikb-token`, `oikb-base-url` | ✅ public projects |
| `gitea:` | `oikb-token`, `oikb-base-url` (required) | ✅ public repos (`oikb-base-url` still required) |
| `bitbucket:` | `oikb-user`, `oikb-app-password` | ✅ public repos (rate-limited) |
| `confluence:` | `oikb-base-url`, `oikb-user`, `oikb-token`, `oikb-api-version` | ❌ |
| `sharepoint:` | `oikb-tenant-id`, `oikb-client-id`, `oikb-client-secret`, `oikb-cloud` | ❌ |

Set `reconciler.allowAnonymous: false` to require credentials for every Knowledge Base.

---

## What is supported, what is not, and why

| Feature | Status | Why / whose limitation |
|---|---|---|
| GitHub, GitLab, Gitea, Bitbucket Cloud, Confluence, SharePoint | ✅ | oikb ≥ 0.5.0 passes per-source credentials (`auth:`) to these connectors |
| Public git repositories without a token | ✅ | Optional (`reconciler.allowAnonymous`) |
| Jira, Notion, Slack, Zendesk, ServiceNow, Linear, Asana, ClickUp, Airtable, HubSpot, Salesforce, Outline, Discourse, BookStack, DokuWiki, Freshdesk, GitBook, Guru, Slab, Document360, Discord, Teams, Zulip, XenForo, Egnyte, Dropbox, Nextcloud, Gong, Fireflies, Google Sites, Google Drive, Gmail, ProductBoard, Zotero, Azure Blob | ❌ | **oikb.** The connectors accept credentials, but oikb 0.6.0's `_resolve_connector` does not pass the `auth:` block to them, so they only read process-wide environment variables, shared by every Knowledge Base. A fix has been requested upstream ([`docs/upstream-feature-request.md`](../../docs/upstream-feature-request.md)) |
| S3, R2, GCS, Oracle Cloud Storage | ❌ | **oikb.** These connectors can only take credentials from the environment / cloud SDK |
| One shared credential for all Knowledge Bases | ❌ (by design) | **This chart.** Any user could then sync content they are not allowed to read |
| Local paths (`./...`), website crawler (`web:`) | ❌ (by design) | **This chart.** They would let users read files inside the pod or reach internal cluster addresses |
| SharePoint certificate authentication | ❌ | **This chart.** Needs a certificate file; the reconciler only passes text values |
| Bitbucket Server / Data Center | ❌ | **oikb.** The connector only talks to Bitbucket Cloud |
| oikb filters (`include`/`exclude`/`max-size`), `concurrency`, `notify`, webhooks | ❌ (not yet) | **This chart.** oikb supports them; the reconciler does not expose them yet |
| Restoring files edited inside Open WebUI | ❌ | **Open WebUI.** The diff compares against the checksum stored at upload time; UI edits do not change it |
| Applying config changes without restarting the daemon | ❌ | **oikb.** The daemon reads its config only at startup |
| More than one daemon replica | ❌ | **oikb.** The scheduler and per-KB locks live in one process |
| External Knowledge Bases | ❌ | **Open WebUI.** They are read-only |

---

## What happens when

### Timings

| What | When |
|---|---|
| Reconciler | Every 5 minutes (`reconciler.schedule`), and once after every `helm install`/`upgrade` |
| Sync after the daemon starts or restarts | Immediately, for every Knowledge Base |
| Regular sync | Every `oikb-interval` (default `1h`) |
| Description change → content updated | Within ~5 minutes, plus the sync itself |
| Push to the source → content updated | At the next interval (no webhooks) |
| Request timeouts | 60 s per request to GitLab, 120 s per request to Open WebUI |

### Events

| Event | Result |
|---|---|
| KB created with `oikb` lines and shared (write) | Next reconciler run: added to the config, daemon restarts, everything is uploaded |
| KB not shared with the service user | Invisible to the reconciler; nothing happens |
| KB shared read-only | Skipped, warning in the reconciler log |
| Unsupported source, or missing required setting | Skipped, warning in the reconciler log |
| `oikb-branch` changed | Restart; content becomes the new branch (only differing files change) |
| `oikb-path` changed | Restart; content is replaced by the new folder's (paths are relative to it) |
| `oikb:` changed to another repository | Restart; content is replaced completely |
| Token rotated, `oikb-interval` changed | Restart; content unchanged |
| Other description text or the KB name changed | Nothing (not even a restart) |
| `oikb:` line removed, or sharing revoked | Sync stops. **Content stays** as it is |
| KB deleted | Removed from the config; daemon restarts |
| No KB left to sync | Daemon scaled to 0 |
| Push to the source / file deleted in the source | Next interval: changed files re-uploaded / file deleted from the KB |
| File edited inside Open WebUI | **Not** restored (one-way sync). Delete it in the KB and the next sync re-adds it |
| File added by hand to a synced KB | **Deleted** on the next sync |
| Wrong branch, expired token, source unreachable | The sync fails during the scan, **before** the diff: **nothing is deleted**. Error in `/health` and the daemon log |
| Config Secret wiped or edited by hand | Repaired on the next reconciler run |

---

## Configuration reference (values)

| Key | Default | Description |
|---|---|---|
| `openWebUI.url` | `""` | **Required.** Open WebUI base URL as reachable from the release namespace |
| `openWebUI.apiKey` | `""` | **Required unless `existingSecret`.** API key of the Open WebUI service user |
| `existingSecret` | `""` | Use this Secret for the API keys instead of creating one |
| `secretKeys.openWebUIApiKey` | `open-webui-api-key` | Key holding the Open WebUI API key |
| `secretKeys.daemonApiKey` | `oikb-api-key` | Key holding the daemon API key |
| `proxy.httpProxy` | `""` | HTTP proxy for outbound traffic |
| `proxy.httpsProxy` | `""` | HTTPS proxy (defaults to `httpProxy`) |
| `proxy.noProxy` | `""` | Extra `NO_PROXY` entries (Open WebUI host, `.svc`, `.cluster.local`, `localhost`, `127.0.0.1` are always added) |
| `platform` | `auto` | `auto` (detect OpenShift), `openshift` (SCC assigns UIDs), `kubernetes` (fixed non-root UIDs 1000 / 65534) |
| `daemon.image.repository` | `ghcr.io/open-webui/oikb` | oikb image |
| `daemon.image.tag` | `""` | Defaults to the chart `appVersion` (`0.6.0`). Must be ≥ 0.5.0 |
| `daemon.image.pullPolicy` | `IfNotPresent` | |
| `daemon.apiKey` | `""` | Key protecting `/sync` and `/history`. Generated and kept across upgrades when empty |
| `daemon.logFormat` | `json` | `json` or `text` |
| `daemon.terminationGracePeriodSeconds` | `60` | Time for in-flight syncs on restart |
| `daemon.resources` | 100m / 256Mi, limit 1Gi | |
| `daemon.service.type` / `.port` | `ClusterIP` / `8080` | Daemon API: `/health`, `/metrics`, `/sync`, `/history` |
| `daemon.persistence.enabled` | `false` | Keep the sync history (SQLite) across restarts in a PVC |
| `daemon.persistence.existingClaim` | `""` | Use an existing PVC |
| `daemon.persistence.storageClass` / `.accessMode` / `.size` | `""` / `ReadWriteOnce` / `100Mi` | |
| `daemon.metrics.serviceMonitor.enabled` | `false` | Create a ServiceMonitor (Prometheus Operator / OpenShift user-workload monitoring) |
| `daemon.metrics.serviceMonitor.interval` / `.labels` | `1m` / `{}` | |
| `daemon.extraEnv` | `[]` | Extra env vars. **Never** source credentials (see [Security](#security)) |
| `daemon.podAnnotations` / `podLabels` / `nodeSelector` / `tolerations` / `affinity` | empty | |
| `reconciler.image.repository` / `.tag` | `docker.io/library/python` / `3.12-alpine` | Any Python 3.9+ image works; the script uses the standard library only |
| `reconciler.schedule` | `*/5 * * * *` | How often descriptions are read |
| `reconciler.suspend` | `false` | Pause the CronJob; the daemon keeps the last config |
| `reconciler.runOnInstall` | `true` | Run once after every `helm install`/`upgrade` (Helm hook). A failure fails the install |
| `reconciler.defaultInterval` | `1h` | Interval for KBs without `oikb-interval` |
| `reconciler.allowAnonymous` | `true` | Allow public git repositories without credentials |
| `reconciler.dryRun` | `false` | Only log the config that would be written (credentials masked) |
| `reconciler.activeDeadlineSeconds` | `120` | Max run time of one reconciler Job |
| `reconciler.successfulJobsHistoryLimit` / `failedJobsHistoryLimit` | `1` / `3` | |
| `reconciler.resources` | 10m / 32Mi, limit 128Mi | |
| `reconciler.podAnnotations` / `podLabels` / `nodeSelector` / `tolerations` / `affinity` | empty | |
| `imagePullSecrets` | `[]` | For both images |
| `nameOverride` / `fullnameOverride` | `""` | |
| `commonLabels` | `{}` | Added to every object |

Values are validated by `values.schema.json`: a typo in a type or an invalid `platform`, URL or interval fails `helm install` with a clear message.

### Objects created

| Object | Name | Content owned by |
|---|---|---|
| Secret | `<release>-keys` (unless `existingSecret`) | Helm |
| Secret | `<release>-config` | **Reconciler** (Helm creates it empty) |
| Deployment, Service | `<release>` | Helm, except `replicas` and the `oikb.openwebui.com/config-hash` pod annotation (**reconciler**) |
| ServiceAccount, Role, RoleBinding, ConfigMap (script), CronJob | `<release>-reconciler` | Helm |
| Job (Helm hook) | `<release>-reconciler-init` | Helm |
| PVC, ServiceMonitor | `<release>-data`, `<release>` | Helm (optional) |

`<release>` is the release name, or `<release>-oikb-sync` when the release name does not contain `oikb-sync`.

---

## Security

### Where data is stored

| Data | Where | Contains credentials |
|---|---|---|
| Source and credentials, as typed by the user | Open WebUI database, KB description | **yes** |
| Generated `.oikb.yaml` | Secret `<release>-config` (etcd) | **yes** |
| `.oikb.yaml` inside the daemon pod | Secret volume (in-memory tmpfs) | **yes** |
| API keys | Secret `<release>-keys` or `existingSecret` (etcd) | **yes** |
| Sync history | SQLite in `/data` (emptyDir, or the PVC) | no |
| Logs | stdout | no (credentials are masked) |

oikb has **no database of its own for configuration or credentials**. The config Secret is created without data and filled only by the reconciler, so credentials never end up in Helm's release history.

### Know the risks

- **Credentials in the description are visible to everyone who can read the Knowledge Base:** users it is shared with, and admins. Use **least-privilege, read-only tokens** scoped to one repository/project/space, and do not share a synced Knowledge Base with people who must not see the token. Open WebUI's built-in knowledge tools may also show descriptions to the model.
- **Kubernetes Secrets are base64, not encrypted**, unless etcd encryption is on. OpenShift: `oc get apiserver cluster -o jsonpath='{.spec.encryption.type}'` (empty = off; ask the cluster admins for `aescbc`/`aesgcm`). Limit who can read Secrets in the namespace.
- **Never give the daemon global source credentials** (`GITHUB_TOKEN`, `GITLAB_TOKEN`, ... via `daemon.extraEnv`). A Knowledge Base without credentials falls back to them, so any user could sync any repository they can read.
- **Service user:** a regular user sees only the Knowledge Bases shared with it. An admin service user (with Open WebUI's default `BYPASS_ADMIN_ACCESS_CONTROL=true`) would see and write every Knowledge Base without sharing, but its API key would control all of Open WebUI. A regular user is recommended.

### What the chart does

- Reconciler RBAC: `get`/`update` on one Secret and `get`/`patch` on one Deployment, by name. Nothing else.
- Only six connectors; local paths and `web:` are rejected.
- Credentials never appear in logs; `dryRun` masks them.
- Non-root, no privilege escalation, all capabilities dropped, `RuntimeDefault` seccomp, read-only root filesystem for the reconciler; no service-account token in the daemon pod.
- The daemon's `/sync` and `/history` endpoints require the daemon API key (read from a file).
- The Open WebUI API key only grants what the service user has: oikb cannot bypass Knowledge Base access control.

---

## Operations

```bash
NS=oikb; REL=oikb-sync

# Run the reconciler now
kubectl -n $NS create job --from=cronjob/$REL-reconciler $REL-reconcile-now
kubectl -n $NS logs job/$REL-reconcile-now

# Daemon
kubectl -n $NS get deploy $REL
kubectl -n $NS logs deploy/$REL

# Daemon API
kubectl -n $NS port-forward svc/$REL 8080:8080 &
curl -s localhost:8080/health                       # status of every KB (public)
curl -s localhost:8080/metrics                      # Prometheus metrics (public)
KEY=$(kubectl -n $NS get secret $REL-keys -o jsonpath='{.data.oikb-api-key}' | base64 -d)
curl -s -H "Authorization: Bearer $KEY" localhost:8080/history
curl -s -X POST -H "Authorization: Bearer $KEY" localhost:8080/sync/kb-<kb-id>   # sync one KB now

# The generated config (shows credentials!)
kubectl -n $NS get secret $REL-config -o jsonpath='{.data.\.oikb\.yaml}' | base64 -d
```

Each Knowledge Base appears in the config as `kb-<kb-id>`. The daemon also serves a status page at `/`.

Metrics: `oikb_sync_total`, `oikb_sync_errors_total`, `oikb_sync_duration_seconds`, `oikb_files_uploaded_total`, `oikb_files_deleted_total`.

**Dry run:** `--set reconciler.dryRun=true` logs the config that would be written, with credentials masked, and changes nothing.

---

## GitOps (Argo CD)

Argo CD renders charts with `helm template`, where `lookup` returns nothing. The Deployment's `replicas` and config-hash annotation would then be rendered as `0` / absent, and Argo CD would undo the reconciler's changes. Tell Argo CD to leave them alone:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: oikb-sync
spec:
  source:
    repoURL: <this repository>
    path: charts/oikb-sync
    helm:
      valueFiles: [my-values.yaml]
  destination:
    namespace: oikb
  ignoreDifferences:
    - group: apps
      kind: Deployment
      jsonPointers:
        - /spec/replicas
        - /spec/template/metadata/annotations/oikb.openwebui.com~1config-hash
  syncPolicy:
    syncOptions:
      - RespectIgnoreDifferences=true
```

- The post-install/post-upgrade hook Job runs as an Argo CD `PostSync` hook.
- The config Secret is rendered without data, so Argo CD does not touch the reconciler's content.
- Keep API keys out of Git: use `existingSecret` with Sealed Secrets or External Secrets.

---

## Troubleshooting

| Symptom | Check / cause |
|---|---|
| `helm install` fails on the hook Job | `kubectl -n oikb logs job/<release>-reconciler-init`: wrong `openWebUI.url`, wrong API key, no network/proxy |
| A KB is never synced | Reconciler log: a `WARN` line says why (no write access, unsupported source, missing setting). No line at all = the KB is not shared with the service user, or its description has no `oikb:` line |
| Daemon at 0 replicas | No KB qualifies yet; see the reconciler log |
| `Sync failed for kb-...: timeout` | The pod cannot reach the source: set `proxy.*`, or allow egress (EgressFirewall / NetworkPolicy) |
| `401`/`403`/`404` from the source | Wrong or expired token, missing scope (`read_api` for GitLab, Contents read for GitHub), wrong path or branch |
| GitHub `rate limit exceeded` | Public repo without a token: add `oikb-token` |
| Reconciler `cannot reach Open WebUI` | `openWebUI.url` wrong, or the proxy catches it (the chart adds the Open WebUI host to `NO_PROXY`; check `proxy.noProxy`) |
| Reconciler `rejected the API key` | Wrong key in the keys Secret; API keys disabled (`ENABLE_API_KEYS`); the service user lacks the *API Keys* feature permission; or endpoint restrictions block `/api/v1/knowledge` / `/api/v1/files`. See [Prepare Open WebUI](#prepare-open-webui-once) |
| A change in the source did not arrive | Next interval not reached; trigger `/sync/kb-<id>` |
| A file edited in Open WebUI was not restored | Expected (one-way). Delete it in the KB |
| A file added by hand disappeared | Expected (the KB mirrors its source) |
| Pod rejected on OpenShift: `unable to validate against any security context constraint` | Set `platform: openshift` (if detection failed, e.g. with restricted API discovery) |
| Pod rejected on Kubernetes: `runAsNonRoot ... non-numeric user` | Set `platform: kubernetes` |

---

## Upgrading and uninstalling

```bash
helm upgrade oikb-sync ./charts/oikb-sync -n oikb -f my-values.yaml
helm uninstall oikb-sync -n oikb
```

- Upgrades keep the reconciler's state (config, replicas, generated daemon key). The hook Job then reconciles immediately.
- Do not use `helm upgrade --force`: it recreates objects. (If you do, the next reconciler run repairs the config.)
- Uninstall removes everything the chart created, including the config Secret. **The Knowledge Bases and their files in Open WebUI are not touched.**
