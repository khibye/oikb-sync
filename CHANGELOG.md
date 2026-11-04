# Changelog

All notable changes to the oikb-sync chart. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [0.1.0] - 2026-11-04

Initial release. oikb `0.6.0`.

### Added

- oikb daemon Deployment (0 or 1 replica, `Recreate`), Service, optional PVC for sync history, optional ServiceMonitor.
- Reconciler CronJob (every 5 minutes) and a post-install/post-upgrade hook Job, with least-privilege RBAC (one Secret, one Deployment, by name).
- Knowledge Base description format: `oikb:` source plus `oikb-branch`, `oikb-path`, `oikb-interval` and per-source credential lines. Parses descriptions flattened to one line by Open WebUI's edit page.
- Sources: GitHub, GitLab, Gitea, Bitbucket Cloud, Confluence, SharePoint; public git repositories without credentials (`reconciler.allowAnonymous`).
- Validation of required settings per source (e.g. Gitea `oikb-base-url`, SharePoint tenant/client/secret) with clear warnings.
- Self-healing: a wiped or hand-edited config Secret is repaired on the next run.
- OpenShift auto-detection (`platform: auto`), HTTP proxy support with an automatic `NO_PROXY`, `existingSecret`, `dryRun`, `suspend`, values schema.
- Documentation in English and Hebrew; CI with helm lint, kubeconform and tests.
