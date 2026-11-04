**Category:** Ideas
**Title:** Per-source `auth:` for every connector

---

### Summary

Per-source credentials (`auth:` in `.oikb.yaml`, `--auth` on the CLI) currently reach only 6 of the 47 connectors. Most of the others already accept credentials in their constructors, but `_resolve_connector` never passes them, so they can only use global environment variables. This request is to make `auth:` work for **every** connector, so any source can be configured with its own credentials.

### Use case

We run Open WebUI on Kubernetes for many users, with one oikb daemon. Each Knowledge Base is synced from a source chosen by its owner, using **that owner's** credentials. Environment variables are process-wide, so with a global `JIRA_TOKEN` or `NOTION_TOKEN`:

- a private source can only be synced if one shared service account can read it, and
- if it can, any user can point a KB at content they are not allowed to see.

Per-source `auth:` solves this, and it already works well for GitHub, GitLab, Gitea, Bitbucket, SharePoint and Confluence (thanks for 0.5.0 and the #118 fix). We would like the same for the rest.

### Current behavior (main @ `29b0e45`, v0.6.0)

`_resolve_connector` in `src/oikb/cli.py` forwards `auth` to GitHub, GitLab, Gitea, Bitbucket and SharePoint (`**auth`), and to Confluence (selected keys). Every other connector is constructed **without** `auth`, so its credentials always come from environment variables.

The function's own docstring describes the intended design: *"This function stays agnostic about what any one connector needs: it just forwards the dict as \*\*auth and lets the connector's own \_\_init\_\_ validate it (and fall back to its env vars when a key is missing, same as today)."* That just isn't applied to most branches yet.

#### A. Constructor already accepts credentials; resolver doesn't pass `auth`

These need only a resolver change:

| Connector | Credential parameters already in `__init__` |
|---|---|
| Airtable | `token` |
| Asana | `token` |
| Azure Blob | `connection_string` |
| BookStack | `base_url`, `token_id`, `token_secret` |
| ClickUp | `token` |
| Discord | `token` |
| Discourse | `base_url`, `api_key`, `username` |
| Document360 | `token` |
| DokuWiki | `base_url`, `user`, `password` |
| Dropbox | `token` |
| Egnyte | `domain`, `token` |
| Fireflies | `token` |
| Freshdesk | `domain`, `token` |
| GitBook | `token` |
| Gmail | `service_account_file` |
| Gong | `access_key`, `secret` |
| Google Drive | `service_account_file` |
| Google Sites | `token` |
| Guru | `user`, `token` |
| HubSpot | `token` |
| Jira | `base_url`, `user`, `token` |
| Linear | `token` |
| Nextcloud | `base_url`, `user`, `password` |
| Notion | `token` |
| Outline | `base_url`, `token` |
| ProductBoard | `token` |
| Salesforce | `instance_url`, `token` |
| ServiceNow | `instance`, `user`, `password` |
| Slab | `org`, `token` |
| Slack | `token` |
| Teams | `tenant_id`, `client_id`, `client_secret` |
| XenForo | `base_url`, `token` |
| Zendesk | `subdomain`, `user`, `token` |
| Zotero | `library_id`, `library_type`, `api_key` |

#### B. Some credentials can only come from the environment

These also need constructor changes:

| Connector | Accepted today | Environment / SDK only |
|---|---|---|
| S3 | `region` | access key, secret key, session token, endpoint (boto3 default chain) |
| R2 | `account_id` | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |
| GCS | none | service-account credentials (`GOOGLE_APPLICATION_CREDENTIALS`) |
| Oracle Cloud | `namespace` | OCI SDK config / profile |
| Zulip | `token`, `base_url` | `ZULIP_EMAIL` |

### Proposal

1. **Forward `auth` to every connector in `_resolve_connector`**, merged with the values parsed from the source string. Merge, rather than pass both, so a key that appears in both places (e.g. Zendesk `subdomain`, Freshdesk `domain`) doesn't raise a duplicate-keyword `TypeError`; `auth` should win for credential fields. Keep the environment-variable fallback, so nothing changes for existing configs.
2. **Add explicit credential parameters to the connectors in table B**, with the environment as fallback:
   - S3: `access_key_id`, `secret_access_key`, `session_token`, `endpoint_url`
   - R2: `access_key_id`, `secret_access_key`
   - GCS: `service_account_file`
   - Oracle: `config_file`, `profile`
   - Zulip: `email`
3. **Clear validation:** an unknown `auth` key should produce a readable error in `oikb validate`, naming the connector and its accepted keys, instead of a raw `TypeError`.
4. **Document the accepted `auth` keys per connector** in the guide, e.g. as a column in the connectors table.
5. **Optional:** `*_file` variants (e.g. `token_file`) so credentials can be mounted from Docker/Kubernetes secrets without going through environment variables, consistent with the existing `OIKB_API_KEY_FILE`.

### Why it matters

- Per-source credentials are what make one oikb daemon safe to share between teams or users. Without them, a shared credential has to be able to read everything.
- It makes behavior consistent: today the same `auth:` block works for GitHub and is silently ignored for Jira or Notion, with no error.
- Most of the work is already done in the connectors; part 1 is a small, mechanical change.

We're happy to contribute a PR for part 1, with tests showing that `auth` overrides the environment for each connector, if the approach looks good to you.
