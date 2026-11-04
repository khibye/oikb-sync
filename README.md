# oikb-sync

**Self-service sync of Open WebUI Knowledge Bases from GitHub, GitLab, Gitea, Bitbucket, Confluence and SharePoint, as a Helm chart.**

Users create a Knowledge Base in [Open WebUI](https://github.com/open-webui/open-webui) and write its source and credentials in the description:

```
oikb: gitlab:platform/ops/runbooks
oikb-base-url: https://gitlab.example.com
oikb-token: glpat-xxxxxxxxxxxxxxxxxxxx
```

A small reconciler turns those descriptions into the configuration of [oikb](https://github.com/open-webui/oikb), the official Open WebUI sync tool, whose daemon keeps every Knowledge Base in sync with its source. No YAML editing, no redeploys.

```mermaid
flowchart LR
    U[User] -->|source + credentials<br/>in the KB description| OW[Open WebUI]
    OW -->|list KBs| R[Reconciler<br/>every 5 min]
    R -->|.oikb.yaml| D[oikb daemon]
    SRC[GitHub / GitLab / Gitea /<br/>Bitbucket / Confluence / SharePoint] -->|per-KB credentials| D
    D -->|incremental sync| OW
```

## Quick start

1. In Open WebUI, prepare a service user with an API key. API keys are off by default: see [Prepare Open WebUI](charts/oikb-sync/README.md#prepare-open-webui-once).
2. Install:

   ```bash
   helm install oikb-sync ./charts/oikb-sync -n oikb --create-namespace \
     --set openWebUI.url=http://open-webui.open-webui.svc.cluster.local:8080 \
     --set openWebUI.apiKey=sk-...
   ```

3. Users add `oikb` lines to a Knowledge Base description and share it with the service user (write). Within 5 minutes it is synced.

Works on OpenShift and plain Kubernetes; the platform is detected automatically.

## Documentation

| Document | For |
|---|---|
| [`charts/oikb-sync/README.md`](charts/oikb-sync/README.md) | Full reference (English): installation, every source and setting, what is supported and why, security, operations, GitOps, troubleshooting |
| [`docs/he/README.md`](docs/he/README.md) | מדריך מלא בעברית |
| [`docs/upstream-feature-request.md`](docs/upstream-feature-request.md) | Request to oikb to support per-source credentials for all connectors |
| [`CHANGELOG.md`](CHANGELOG.md) | Chart releases |

## Supported sources

| Source | Private (with credentials) | Public (without) |
|---|---|---|
| GitHub | ✅ | ✅ (rate-limited) |
| GitLab (gitlab.com, self-managed) | ✅ | ✅ |
| Gitea | ✅ | ✅ |
| Bitbucket Cloud | ✅ | ✅ (rate-limited) |
| Confluence (Cloud, Data Center) | ✅ | – |
| SharePoint Online | ✅ | – |

Other oikb connectors (Jira, Notion, Slack, S3, ...) are not supported yet, because oikb 0.6.0 cannot give them per-Knowledge-Base credentials. [Details](charts/oikb-sync/README.md#what-is-supported-what-is-not-and-why).

## Repository layout

```
charts/oikb-sync/          the Helm chart
  files/reconciler.py      the reconciler (Python, standard library only)
  templates/               Kubernetes objects
  values.yaml              defaults, with comments
  values.schema.json       values validation
  ci/                      values used by CI
docs/he/README.md          Hebrew guide
tests/test_reconciler.py   reconciler tests (unit + end-to-end against fake APIs)
.github/workflows/ci.yaml  lint, render, schema validation, tests
```

## Development

```bash
make test       # reconciler tests (Python 3.9+, no dependencies)
make lint       # helm lint (Kubernetes and OpenShift values)
make template   # render the chart to stdout
```

CI runs the same steps plus [kubeconform](https://github.com/yannh/kubeconform) schema validation on every push and pull request.

Versioning: bump `version` in `charts/oikb-sync/Chart.yaml` and add a `CHANGELOG.md` entry for every chart change; `appVersion` follows the oikb release.
