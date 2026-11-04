#!/usr/bin/env python3
"""oikb-sync reconciler: a pipe from Open WebUI Knowledge Base descriptions to oikb config.

Runs once per invocation (a Kubernetes CronJob, plus a Helm hook after install and
upgrade). Each run:

  1. Lists the Knowledge Bases the service user can see in Open WebUI.
  2. Reads the `oikb...` lines from each description (source, options, credentials).
  3. Renders one .oikb.yaml from them.
  4. If it differs from what the daemon runs (a hash kept as an annotation on the
     daemon's pod template), writes it to the config Secret and restarts the daemon,
     or scales the daemon to 0 when there is nothing to sync (oikb refuses to start
     with an empty config).

The reconciler never syncs anything itself; the oikb daemon does all of that.
Standard library only, so it runs on a plain python image.

Knowledge Base description format (other text in the description is ignored):

    oikb: github:my-org/docs        required: the oikb source string
    oikb-branch: main               optional
    oikb-path: docs/                optional
    oikb-interval: 1h               optional
    oikb-token: ghp_xxx             credentials, per connector (see AUTH_FIELDS);
    oikb-base-url: https://...      dashes become underscores (base_url)

Environment:

    OPEN_WEBUI_URL, OPEN_WEBUI_API_KEY   required
    OIKB_DEPLOYMENT                      daemon Deployment name     (default: oikb)
    OIKB_CONFIG_SECRET                   config Secret name         (default: oikb-config)
    OIKB_DEFAULT_INTERVAL                interval when not set      (default: 1h)
    OIKB_ALLOW_ANONYMOUS                 public repos w/o token     (default: true)
    DRY_RUN                              log only, change nothing   (default: false)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

# Connectors whose oikb constructor receives per-source credentials (oikb >= 0.5.0),
# and the credential fields each one accepts. Sources using any other connector are
# skipped: oikb would read their credentials from process-wide environment variables,
# shared by every Knowledge Base.
AUTH_FIELDS: dict[str, set[str]] = {
    "github:": {"token"},
    "gitlab:": {"token", "base_url"},
    "gitea:": {"token", "base_url"},
    "bitbucket:": {"user", "app_password"},
    "sharepoint:": {"tenant_id", "client_id", "client_secret", "cloud"},
    "confluence:": {"base_url", "user", "token", "api_version"},
}
ENTRY_FIELDS = {"branch", "path", "interval"}
# Git connectors that can read public repositories without credentials. Such an
# entry has no auth: block, so the connector falls back to its global env var
# (GITHUB_TOKEN, ...): the daemon must therefore never set those env vars.
ANONYMOUS_OK = {"github:", "gitlab:", "gitea:", "bitbucket:"}

HASH_ANNOTATION = "oikb.openwebui.com/config-hash"


def missing_settings(prefix: str, source: str, auth: dict[str, str]) -> str | None:
    """What a connector still needs, or None. Catches here what oikb would only
    report later as a failed sync."""
    if prefix == "gitea:" and "base_url" not in auth:
        return "oikb-base-url (Gitea has no default instance)"
    if prefix == "bitbucket:" and len({"user", "app_password"} & set(auth)) == 1:
        return "both oikb-user and oikb-app-password (or neither, for a public repository)"
    if prefix == "sharepoint:":
        absent = [f for f in ("tenant_id", "client_id", "client_secret") if f not in auth]
        if absent:
            return ", ".join("oikb-" + f.replace("_", "-") for f in absent)
    if prefix == "confluence:":
        absent = [] if "token" in auth else ["oikb-token"]
        if "base_url" not in auth and not source.startswith(("confluence:http://", "confluence:https://")):
            absent.append("oikb-base-url")
        if absent:
            return ", ".join(absent)
    return None

# `oikb[-key]: value` pairs. A value runs until the next pair or the end of the line.
#
# Open WebUI's KB *edit* page shows the description in a single-line <input>, and
# browsers strip line breaks from it: after a user edits the description there,
# "...docs\noikb-token: x" becomes "...docsoikb-token: x". So known keys are
# recognised anywhere, even glued to the previous text. Unknown `oikb-*` keys are
# recognised only at the start of a line or after whitespace (and then warned about).
_KNOWN_KEYS = sorted({f.replace("_", "-") for f in ENTRY_FIELDS.union(*AUTH_FIELDS.values())}, key=len, reverse=True)
KEY_RE = re.compile(
    rf"(?:oikb(?:-({'|'.join(map(re.escape, _KNOWN_KEYS))}))?|(?:(?<=\s)|^)oikb-([a-z][a-z0-9-]*))\s*:",
    re.IGNORECASE,
)

TRUE_VALUES = ("1", "true", "yes", "on")


def log(msg: str) -> None:
    print(msg, flush=True)


def env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None or value == "" else value.strip().lower() in TRUE_VALUES


# ── pure logic ──────────────────────────────────────────────────────────────

def iter_pairs(text: str):
    """Yield (key, value) for every oikb pair; key is '' for the bare `oikb:` source."""
    for line in text.splitlines():
        matches = list(KEY_RE.finditer(line))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            value = line[m.end():end].strip()
            if value:
                yield (m.group(1) or m.group(2) or ""), value


def parse_description(description: str | None) -> dict[str, str]:
    """Return {"source": ..., other fields...} from `oikb...` pairs, or {} if no source.

    `oikb: X` sets the source; `oikb-some-key: Y` sets some_key. First occurrence wins.
    Pairs may be one per line or several on one line.
    """
    found: dict[str, str] = {}
    for key, value in iter_pairs(description or ""):
        field = key.lower().replace("-", "_") if key else "source"
        found.setdefault(field, value)
    return found if "source" in found else {}


def connector_prefix(source: str) -> str | None:
    for prefix in AUTH_FIELDS:
        if source.startswith(prefix):
            return prefix
    return None


def build_entries(kbs: list[dict], allow_anonymous: bool = True) -> tuple[list[dict], list[str]]:
    """Turn Knowledge Bases into oikb source entries. Returns (entries, warnings)."""
    entries: list[dict] = []
    warnings: list[str] = []

    for kb in sorted(kbs, key=lambda k: k["id"]):
        kb_id = kb["id"]
        settings = parse_description(kb.get("description"))
        if not settings:
            continue
        label = f"KB '{kb.get('name', '')}' ({kb_id})"

        if (kb.get("meta") or {}).get("source") == "external":
            warnings.append(f"{label}: external Knowledge Bases are read-only, skipped")
            continue
        if kb.get("write_access") is False:
            warnings.append(f"{label}: the service user has no write access "
                            "(share the KB with it, with write permission), skipped")
            continue

        source = settings.pop("source")
        prefix = connector_prefix(source)
        if prefix is None:
            warnings.append(f"{label}: source '{source}' is not supported "
                            f"(supported: {', '.join(sorted(AUTH_FIELDS))}), skipped")
            continue

        entry: dict = {"name": f"kb-{kb_id}", "source": source, "kb-id": kb_id}
        auth: dict[str, str] = {}
        for field, value in sorted(settings.items()):
            if field in ENTRY_FIELDS:
                entry[field] = value
            elif field in AUTH_FIELDS[prefix]:
                auth[field] = value
            else:
                warnings.append(f"{label}: ignoring unknown line 'oikb-{field.replace('_', '-')}' for a {prefix} source")
        if not auth and not (allow_anonymous and prefix in ANONYMOUS_OK):
            expected = ", ".join("oikb-" + f.replace("_", "-") for f in sorted(AUTH_FIELDS[prefix]))
            warnings.append(f"{label}: no credentials in the description (expected: {expected}), skipped")
            continue
        missing = missing_settings(prefix, source, auth)
        if missing:
            warnings.append(f"{label}: missing {missing}, skipped")
            continue
        if auth:
            entry["auth"] = auth
        entries.append(entry)

    return entries, warnings


def render_config(entries: list[dict], default_interval: str) -> str:
    """Render .oikb.yaml. JSON is valid YAML, so no yaml library is needed."""
    return json.dumps({"defaults": {"interval": default_interval}, "sources": entries}, indent=2, sort_keys=True) + "\n"


def redact(entries: list[dict]) -> list[dict]:
    """Copy of entries with credential values masked, for logs."""
    return [{**e, "auth": {k: "***" for k in e["auth"]}} if "auth" in e else dict(e) for e in entries]


def config_hash(config: str) -> str:
    return hashlib.sha256(config.encode()).hexdigest()[:16]


# ── HTTP ────────────────────────────────────────────────────────────────────

def _request(opener, method: str, url: str, headers: dict, body: dict | None = None,
             content_type: str = "application/json"):
    """Send a JSON request. Returns the decoded body, or None on 404."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json", **headers}
    if data is not None:
        hdrs["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with opener.open(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{method} {url} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"{method} {url} -> {e.reason}") from e


# ── Open WebUI ──────────────────────────────────────────────────────────────

def list_knowledge_bases(base_url: str, api_key: str) -> list[dict]:
    """All Knowledge Bases visible to the API key's user (paginated)."""
    # Honors HTTP(S)_PROXY / NO_PROXY from the environment.
    opener = urllib.request.build_opener()
    items: list[dict] = []
    page = 1
    while True:
        url = f"{base_url.rstrip('/')}/api/v1/knowledge/?page={page}"
        try:
            data = _request(opener, "GET", url, {"Authorization": f"Bearer {api_key}"})
        except RuntimeError as e:
            if "HTTP 401" in str(e) or "HTTP 403" in str(e):
                raise RuntimeError(
                    f"Open WebUI rejected the API key ({e}). Check: the key in the keys Secret; API keys enabled "
                    "(Admin Settings, ENABLE_API_KEYS); for a non-admin service user, the 'API Keys' feature "
                    "permission; with endpoint restrictions on, /api/v1/knowledge and /api/v1/files allowed") from e
            raise RuntimeError(f"cannot reach Open WebUI at {base_url} ({e}); check openWebUI.url and proxy settings") from e
        if data is None:
            raise RuntimeError(f"{url} not found; is OPEN_WEBUI_URL the Open WebUI base URL (0.9.6 or later)?")
        batch = data.get("items", []) if isinstance(data, dict) else data
        items.extend(batch)
        total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
        if not batch or len(items) >= total:
            return items
        page += 1


# ── Kubernetes API ──────────────────────────────────────────────────────────

class Kube:
    """Minimal in-cluster Kubernetes client (Secrets + Deployments in one namespace)."""

    SA = "/var/run/secrets/kubernetes.io/serviceaccount"

    def __init__(self, base: str | None = None, token: str | None = None,
                 namespace: str | None = None, ctx: ssl.SSLContext | None = None) -> None:
        if base is None:
            host = os.environ["KUBERNETES_SERVICE_HOST"]
            port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
            base = f"https://[{host}]:{port}" if ":" in host else f"https://{host}:{port}"
            ctx = ssl.create_default_context(cafile=f"{self.SA}/ca.crt")
        if token is None:
            with open(f"{self.SA}/token") as f:
                token = f.read().strip()
        if namespace is None:
            with open(f"{self.SA}/namespace") as f:
                namespace = f.read().strip()
        self.base, self.token, self.namespace = base.rstrip("/"), token, namespace
        # The API server is in-cluster: never send it through an HTTP proxy.
        handlers: list = [urllib.request.ProxyHandler({})]
        if ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self.opener = urllib.request.build_opener(*handlers)

    def call(self, method: str, path: str, body: dict | None = None, content_type: str = "application/json"):
        return _request(self.opener, method, self.base + path,
                        {"Authorization": f"Bearer {self.token}"}, body, content_type)

    def read_secret(self, name: str) -> dict[str, str] | None:
        """Decoded data of a Secret, or None if it does not exist."""
        current = self.call("GET", f"/api/v1/namespaces/{self.namespace}/secrets/{name}")
        if current is None:
            return None
        return {k: base64.b64decode(v).decode() for k, v in (current.get("data") or {}).items()}

    def write_secret(self, name: str, data: dict[str, str]) -> None:
        """Replace the Secret's data (so removed KBs disappear), keeping its metadata.

        The Secret is created by the Helm chart; the reconciler only has get/update on
        it. Labels and annotations are kept so Helm keeps owning the object.
        """
        path = f"/api/v1/namespaces/{self.namespace}/secrets/{name}"
        current = self.call("GET", path)
        if current is None:
            raise RuntimeError(f"Secret {name} not found in namespace {self.namespace} (it is created by the Helm chart)")
        meta = current.get("metadata", {})
        self.call("PUT", path, {
            "apiVersion": "v1", "kind": "Secret", "type": current.get("type", "Opaque"),
            "metadata": {"name": name, "namespace": self.namespace,
                         "resourceVersion": meta.get("resourceVersion"),
                         "labels": meta.get("labels") or {}, "annotations": meta.get("annotations") or {}},
            "stringData": data})

    def deployment(self, name: str) -> dict:
        dep = self.call("GET", f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}")
        if dep is None:
            raise RuntimeError(f"Deployment {name} not found in namespace {self.namespace}")
        return dep

    def patch_deployment(self, name: str, replicas: int, digest: str) -> None:
        self.call("PATCH", f"/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}", {
            "spec": {"replicas": replicas,
                     "template": {"metadata": {"annotations": {HASH_ANNOTATION: digest}}}}},
            content_type="application/strategic-merge-patch+json")


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    owui_url = os.environ["OPEN_WEBUI_URL"]
    owui_key = os.environ["OPEN_WEBUI_API_KEY"]
    deployment = os.environ.get("OIKB_DEPLOYMENT", "oikb")
    config_secret = os.environ.get("OIKB_CONFIG_SECRET", "oikb-config")
    default_interval = os.environ.get("OIKB_DEFAULT_INTERVAL", "1h")
    allow_anonymous = env_flag("OIKB_ALLOW_ANONYMOUS", True)
    dry_run = env_flag("DRY_RUN", False)

    kube = Kube()
    kbs = list_knowledge_bases(owui_url, owui_key)
    entries, warnings = build_entries(kbs, allow_anonymous=allow_anonymous)
    for w in warnings:
        log(f"WARN: {w}")

    config = render_config(entries, default_interval)
    digest = config_hash(config)
    replicas = 1 if entries else 0

    dep = kube.deployment(deployment)
    annotations = (dep.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations") or {})
    current = annotations.get(HASH_ANNOTATION)
    stored = kube.read_secret(config_secret)
    if stored is None:
        raise RuntimeError(f"Secret {config_secret} not found in namespace {kube.namespace} (it is created by the Helm chart)")
    log(f"Knowledge Bases visible: {len(kbs)}; synced: {len(entries)} "
        f"[{', '.join(e['name'] for e in entries)}]; config hash {digest} (running: {current})")

    # Compare the Secret's actual content too, so a wiped or hand-edited Secret is repaired.
    if current == digest and dep.get("spec", {}).get("replicas") == replicas and stored.get(".oikb.yaml") == config:
        log("No changes.")
        return 0
    if dry_run:
        log("DRY_RUN: would write this config (credentials masked) and restart the daemon:\n"
            + render_config(redact(entries), default_interval))
        return 0

    kube.write_secret(config_secret, {".oikb.yaml": config})
    kube.patch_deployment(deployment, replicas, digest)
    log(f"Applied: {len(entries)} source(s); daemon replicas={replicas}, config hash {digest}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # one clean line in the Job log
        log(f"ERROR: {exc}")
        sys.exit(1)
