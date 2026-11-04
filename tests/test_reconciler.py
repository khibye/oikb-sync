"""Tests for charts/oikb-sync/files/reconciler.py. Standard library only.

    python -m unittest discover -s tests -v
"""
import base64
import contextlib
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "charts" / "oikb-sync" / "files"))
import reconciler as r  # noqa: E402

KBS = [
    {"id": "aaaa-1", "name": "docs", "write_access": True,
     "description": "Team docs\noikb: github:acme/docs\noikb-branch: main\noikb-path: docs/\n"
                    "OIKB-Interval: 30m\noikb-token: ghp_secret\noikb-junk: x"},
    {"id": "bbbb-2", "name": "wiki", "write_access": True,
     "description": "oikb: confluence:ENG\noikb-base-url: https://acme.atlassian.net/wiki\n"
                    "oikb-user: u@acme.com\noikb-token: atl"},
    {"id": "cccc-3", "name": "no-line", "write_access": True, "description": "just notes"},
    {"id": "dddd-4", "name": "s3", "write_access": True, "description": "oikb: s3://bucket/x\noikb-token: t"},
    {"id": "eeee-5", "name": "local", "write_access": True, "description": "oikb: ./etc\noikb-token: t"},
    {"id": "ffff-6", "name": "public", "write_access": True, "description": "oikb: github:acme/public-docs"},
    {"id": "gggg-7", "name": "readonly", "write_access": False, "description": "oikb: github:acme/y\noikb-token: t"},
    {"id": "hhhh-8", "name": "ext", "write_access": True, "meta": {"source": "external"},
     "description": "oikb: github:acme/z\noikb-token: t"},
    {"id": "iiii-9", "name": "web", "write_access": True, "description": "oikb: web:http://10.0.0.1\noikb-token: t"},
    {"id": "jjjj-10", "name": "nocreds", "write_access": True, "description": "oikb: confluence:ENG"},
]


class ParseDescription(unittest.TestCase):
    def test_basics(self):
        self.assertEqual(r.parse_description("hello"), {})
        self.assertEqual(r.parse_description(None), {})
        self.assertEqual(r.parse_description("oikb-branch: main"), {})  # no source
        self.assertEqual(r.parse_description("x\n  oikb:  github:a/b  \n"), {"source": "github:a/b"})
        self.assertEqual(r.parse_description("oikb: github:a/b\noikb: gitlab:c/d")["source"], "github:a/b")
        self.assertEqual(r.parse_description("oikb: gitlab:a/b\noikb-base-url: https://g.x")["base_url"], "https://g.x")
        self.assertEqual(r.parse_description("oikb: a\noikb-token: abc:def")["token"], "abc:def")

    def test_single_line_after_edit_page(self):
        multi = ("Runbooks\noikb: gitlab:platform/ops/runbooks\noikb-base-url: https://gitlab.example.com\n"
                 "oikb-interval: 0 6 * * 1-5\noikb-token: glpat-abc")
        expected = {"source": "gitlab:platform/ops/runbooks", "base_url": "https://gitlab.example.com",
                    "interval": "0 6 * * 1-5", "token": "glpat-abc"}
        self.assertEqual(r.parse_description(multi), expected)
        self.assertEqual(r.parse_description(multi.replace("\n", "")), expected)  # browser strips breaks
        self.assertEqual(r.parse_description(multi.replace("\n", " ")), expected)
        self.assertEqual(r.parse_description(multi.replace("\n", "\r\n")), expected)

    def test_no_leaks_between_fields(self):
        self.assertEqual(r.parse_description("oikb: gitlab:a/b oikb-token: glpat-x"),
                         {"source": "gitlab:a/b", "token": "glpat-x"})
        self.assertEqual(r.parse_description("oikb: gitlab:a/b\noikb-junk: x\noikb-token: t"),
                         {"source": "gitlab:a/b", "junk": "x", "token": "t"})
        self.assertEqual(r.parse_description("oikb: gitlab:a/boikb-base-url: https://g.xoikb-token: t"),
                         {"source": "gitlab:a/b", "base_url": "https://g.x", "token": "t"})


class BuildEntries(unittest.TestCase):
    def test_entries_and_warnings(self):
        entries, warnings = r.build_entries(KBS)
        by_id = {e["kb-id"]: e for e in entries}
        self.assertEqual(sorted(by_id), ["aaaa-1", "bbbb-2", "ffff-6"])
        self.assertEqual(by_id["aaaa-1"], {"name": "kb-aaaa-1", "source": "github:acme/docs", "kb-id": "aaaa-1",
                                           "branch": "main", "path": "docs/", "interval": "30m",
                                           "auth": {"token": "ghp_secret"}})
        self.assertEqual(by_id["bbbb-2"]["auth"],
                         {"base_url": "https://acme.atlassian.net/wiki", "user": "u@acme.com", "token": "atl"})
        self.assertEqual(by_id["ffff-6"], {"name": "kb-ffff-6", "source": "github:acme/public-docs", "kb-id": "ffff-6"})
        text = "\n".join(warnings)
        for needle in ["oikb-junk", "s3://", "./etc", "web:", "no credentials", "no write access", "external"]:
            self.assertIn(needle, text)
        self.assertNotIn("no-line", text)
        self.assertNotIn("ghp_secret", text)

    def test_incomplete_settings_are_caught(self):
        def kb(desc):
            return {"id": "x", "name": "n", "write_access": True, "description": desc}

        cases = {
            "oikb: gitea:team/docs": "oikb-base-url",
            "oikb: gitea:team/docs\noikb-token: t": "oikb-base-url",
            "oikb: bitbucket:ws/repo\noikb-user: a@b.c": "both oikb-user and oikb-app-password",
            "oikb: sharepoint:contoso.sharepoint.com/Docs\noikb-client-id: c": "oikb-tenant-id, oikb-client-secret",
            "oikb: confluence:ENG\noikb-token: t": "oikb-base-url",
            "oikb: confluence:ENG\noikb-base-url: https://c.x/wiki\noikb-user: u": "oikb-token",
        }
        for desc, needle in cases.items():
            entries, warnings = r.build_entries([kb(desc)])
            self.assertEqual(entries, [], desc)
            self.assertIn(needle, " ".join(warnings), desc)
        ok = [
            "oikb: gitea:team/docs\noikb-base-url: https://git.x",              # public Gitea
            "oikb: bitbucket:ws/repo",                                          # public Bitbucket
            "oikb: bitbucket:ws/repo\noikb-user: a@b.c\noikb-app-password: t",
            "oikb: confluence:https://c.atlassian.net/wiki/ENG\noikb-token: t",  # URL in the source
            "oikb: confluence:ENG\noikb-base-url: https://dc.x/confluence\noikb-token: pat\noikb-api-version: v1",
            "oikb: sharepoint:c.sharepoint.com/sites/Legal/Docs\noikb-tenant-id: t\noikb-client-id: c\noikb-client-secret: s",
        ]
        for desc in ok:
            entries, warnings = r.build_entries([kb(desc)])
            self.assertEqual(len(entries), 1, (desc, warnings))

    def test_anonymous_can_be_disabled(self):
        entries, warnings = r.build_entries(KBS, allow_anonymous=False)
        self.assertNotIn("ffff-6", [e["kb-id"] for e in entries])
        self.assertTrue(any("ffff-6" in w and "no credentials" in w for w in warnings))

    def test_redact_and_hash(self):
        entries, _ = r.build_entries(KBS)
        masked = r.render_config(r.redact(entries), "1h")
        self.assertNotIn("ghp_secret", masked)
        self.assertIn("***", masked)
        cfg = r.render_config(entries, "1h")
        self.assertEqual(json.loads(cfg)["defaults"], {"interval": "1h"})
        rotated = [dict(e, auth={**e["auth"], "token": "new"}) if "auth" in e else e for e in entries]
        self.assertNotEqual(r.config_hash(r.render_config(rotated, "1h")), r.config_hash(cfg))
        self.assertEqual(r.config_hash(cfg), r.config_hash(r.render_config(entries, "1h")))


# ── end-to-end against fake Open WebUI + Kubernetes API servers ─────────────

class FakeCluster:
    """In-memory Open WebUI + Kubernetes API, on one local HTTP server."""

    def __init__(self):
        self.kbs = [dict(k) for k in KBS]
        self.secret = {"metadata": {"name": "rel-oikb-sync-config", "namespace": "ns", "resourceVersion": "1",
                                    "labels": {"app.kubernetes.io/managed-by": "Helm"},
                                    "annotations": {"meta.helm.sh/release-name": "rel"}},
                       "type": "Opaque", "data": {}}
        self.deploy = {"metadata": {"name": "rel-oikb-sync"},
                       "spec": {"replicas": 0, "template": {"metadata": {"annotations": {"keep": "me"}}}}}
        self.requests = []
        cluster = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else None

            def handle_one(self):
                body = self._body()
                cluster.requests.append((self.command, self.path, dict(self.headers), body))
                if self.path.startswith("/api/v1/knowledge/"):
                    if self.headers.get("Authorization") != "Bearer owui-key":
                        return self._send(401, {"detail": "unauthorized"})
                    page = int(self.path.split("page=")[1])
                    items = cluster.kbs[(page - 1) * 4: page * 4]  # 4 per page: exercises pagination
                    return self._send(200, {"items": items, "total": len(cluster.kbs)})
                if self.headers.get("Authorization") != "Bearer kube-token":
                    return self._send(401, {})
                if self.path == "/api/v1/namespaces/ns/secrets/rel-oikb-sync-config":
                    if self.command == "GET":
                        return self._send(200, cluster.secret)
                    if self.command == "PUT":
                        if body["metadata"].get("resourceVersion") != cluster.secret["metadata"]["resourceVersion"]:
                            return self._send(409, {"reason": "Conflict"})
                        # Like the API server: stringData is merged into data (base64) and dropped.
                        data = {k: base64.b64encode(v.encode()).decode() for k, v in body.pop("stringData", {}).items()}
                        cluster.secret = {**body, "data": data, "metadata": {**body["metadata"], "resourceVersion": str(
                            int(cluster.secret["metadata"]["resourceVersion"]) + 1)}}
                        return self._send(200, cluster.secret)
                if self.path == "/apis/apps/v1/namespaces/ns/deployments/rel-oikb-sync":
                    if self.command == "GET":
                        return self._send(200, cluster.deploy)
                    if self.command == "PATCH":
                        assert self.headers["Content-Type"] == "application/strategic-merge-patch+json"
                        cluster.deploy["spec"]["replicas"] = body["spec"]["replicas"]
                        cluster.deploy["spec"]["template"]["metadata"]["annotations"].update(
                            body["spec"]["template"]["metadata"]["annotations"])
                        return self._send(200, cluster.deploy)
                return self._send(404, {})

            do_GET = do_PUT = do_PATCH = do_POST = handle_one

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def config(self) -> str:
        return base64.b64decode(self.secret["data"][".oikb.yaml"]).decode()

    def close(self):
        self.server.shutdown()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.c = FakeCluster()
        self.addCleanup(self.c.close)
        env = {"OPEN_WEBUI_URL": self.c.url, "OPEN_WEBUI_API_KEY": "owui-key",
               "OIKB_DEPLOYMENT": "rel-oikb-sync", "OIKB_CONFIG_SECRET": "rel-oikb-sync-config",
               "OIKB_DEFAULT_INTERVAL": "2h", "DRY_RUN": "",
               # A dead proxy: the Kubernetes calls must bypass it; Open WebUI is in NO_PROXY.
               "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
               "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
               "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        url = self.c.url
        for patcher in (mock.patch.dict(os.environ, env), mock.patch.object(r, "Kube", lambda: _kube(url))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(r.main(), 0)
        return out.getvalue()

    def test_full_cycle(self):
        log = self.run_main()                                            # 1. first run: apply
        self.assertNotIn("ghp_secret", log)
        cfg = json.loads(self.c.config())
        self.assertEqual(cfg["defaults"], {"interval": "2h"})
        self.assertEqual(sorted(e["kb-id"] for e in cfg["sources"]), ["aaaa-1", "bbbb-2", "ffff-6"])
        self.assertEqual(self.c.deploy["spec"]["replicas"], 1)
        self.assertEqual(self.c.deploy["spec"]["template"]["metadata"]["annotations"]["keep"], "me")
        self.assertEqual(self.c.secret["metadata"]["labels"], {"app.kubernetes.io/managed-by": "Helm"})
        self.assertEqual(self.c.secret["metadata"]["annotations"], {"meta.helm.sh/release-name": "rel"})
        self.assertNotIn("POST", [m for m, *_ in self.c.requests])      # never creates objects

        n = len(self.c.requests)
        self.assertIn("No changes.", self.run_main())                    # 2. unchanged: no writes
        self.assertFalse(any(m in ("PUT", "PATCH") for m, *_ in self.c.requests[n:]))

        self.c.kbs[0]["description"] = self.c.kbs[0]["description"].replace("ghp_secret", "ghp_rotated")
        self.run_main()                                                  # 3. token rotated: restart
        self.assertIn("ghp_rotated", self.c.config())

        os.environ["DRY_RUN"] = "true"                                   # 4. dry run: masked, no writes
        self.c.kbs[0]["description"] = self.c.kbs[0]["description"].replace("branch: main", "branch: dev")
        n = len(self.c.requests)
        log = self.run_main()
        self.assertIn('"dev"', log)
        self.assertNotIn("ghp_rotated", log)
        self.assertFalse(any(m in ("PUT", "PATCH") for m, *_ in self.c.requests[n:]))
        os.environ["DRY_RUN"] = ""

        self.c.kbs.clear()                                               # 5. nothing to sync: scale to 0
        self.run_main()
        self.assertEqual(self.c.deploy["spec"]["replicas"], 0)
        self.assertEqual(json.loads(self.c.config())["sources"], [])

    def test_wiped_secret_is_repaired(self):
        self.run_main()
        good = self.c.config()
        self.c.secret["data"] = {}                                       # e.g. helm upgrade --force
        self.assertNotIn("No changes.", self.run_main())
        self.assertEqual(self.c.config(), good)
        self.c.secret["data"][".oikb.yaml"] = base64.b64encode(b"hand edit").decode()
        self.run_main()
        self.assertEqual(self.c.config(), good)

    def test_kube_api_bypasses_proxy(self):
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = ""   # proxy would be used for everything
        self.assertEqual(_kube(self.c.url).deployment("rel-oikb-sync")["metadata"]["name"], "rel-oikb-sync")
        with self.assertRaisesRegex(RuntimeError, "cannot reach Open WebUI"):  # proves the proxy is live
            r.list_knowledge_bases(self.c.url, "owui-key")

    def test_bad_api_key_is_explained(self):
        os.environ["OPEN_WEBUI_API_KEY"] = "wrong"
        with self.assertRaisesRegex(RuntimeError, "rejected the API key"):
            r.main()

    def test_missing_secret_is_explained(self):
        os.environ["OIKB_CONFIG_SECRET"] = "missing"
        with self.assertRaisesRegex(RuntimeError, "Secret missing not found"):
            with contextlib.redirect_stdout(io.StringIO()):
                r.main()


_RealKube = r.Kube


def _kube(url):
    return _RealKube(base=url, token="kube-token", namespace="ns")


if __name__ == "__main__":
    unittest.main()
