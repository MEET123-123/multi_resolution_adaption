"""ASGI API foundation. Single instance; customer portal and production gates remain.

Run behind a TLS reverse proxy after deployment review, not the local preview API.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs

from multires_app.service.saas_store import ServiceStore
from multires_app.service.saas_jobs import JobManager


class SaaSApp:
    def __init__(self, store, manager=None, allowed_hosts=("localhost", "127.0.0.1")):
        self.store = store
        self.manager = manager or JobManager(store)
        self.allowed_hosts = set(allowed_hosts)
        secret = store.root / "preview-signing.key"
        if not secret.exists():
            try:
                with secret.open("xb") as stream:
                    stream.write(secrets.token_bytes(32))
            except FileExistsError:
                pass
        if os.name == "posix":
            secret.chmod(0o600)
        self.signing_key = secret.read_bytes()

    def ticket(self, client, job, revision, expires=None):
        payload = (
            base64.urlsafe_b64encode(
                json.dumps(
                    [client, job, revision, int(expires or time.time() + 600)],
                    separators=(",", ":"),
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(self.signing_key, payload.encode(), hashlib.sha256).hexdigest()
        return payload + "." + signature

    def verify_ticket(self, ticket):
        try:
            payload, signature = ticket.split(".")
            expected = hmac.new(self.signing_key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid preview ticket")
            client, job, revision, expires = json.loads(
                base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            )
            if expires < time.time() or not self.store.client_active(client):
                raise ValueError("expired preview ticket")
            self.store.job(client, job)
            return client, job, revision
        except Exception as exc:
            raise PermissionError("invalid preview ticket") from exc

    async def body(self, receive, maximum):
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise ValueError("request disconnected")
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > maximum:
                raise OverflowError("request exceeds size limit")
            chunks.append(chunk)
            if not message.get("more_body"):
                return b"".join(chunks)

    async def send_json(self, send, status, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(data)).encode()),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": data})

    def artifact(self, client, job, revision, name):
        if not isinstance(revision, int) or revision < 0:
            raise KeyError("artifact not found")
        if "\\" in name or ":" in name or any(p in {"", ".", ".."} for p in name.split("/")):
            raise KeyError("artifact not found")
        reports = {
            "interaction-ir.json",
            "interaction-bindings.json",
            "engine-compatibility.json",
            "scene-analysis.json",
            "anchor-layout.json",
        }
        allowed = (
            name in {"adapted.hwt", "readiness.json", "project/preview.html"}
            or name in {"project/" + r for r in reports}
            or (
                name.startswith("project/assets/")
                and Path(name).suffix.lower()
                in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".mp4", ".m4v", ".webm"}
            )
            or (name.startswith("project/scripts/") and name.endswith(".xml"))
        )
        if not allowed:
            raise KeyError("artifact not found")
        root = (
            self.store.job_root(client, job)
            / "revisions"
            / str(0 if name.startswith("project/assets/") else revision)
        )
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise KeyError("artifact not found")
        return path

    async def send_file(self, send, path):
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = [
            (b"content-type", mime.encode()),
            (b"content-length", str(path.stat().st_size).encode()),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
        ]
        if path.suffix == ".html":
            text = path.read_text(encoding="utf-8")
            scripts = re.findall(r"<script>(.*?)</script>", text, re.S)
            hashes = " ".join(
                "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
                for s in scripts
            )
            csp = (
                "default-src 'none'; script-src "
                + hashes
                + "; style-src 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
            )
            headers.append((b"content-security-policy", csp.encode()))
        elif path.suffix in {".hwt", ".xml"}:
            headers.append(
                (
                    b"content-disposition",
                    b'attachment; filename="' + path.name.encode("ascii", "replace") + b'"',
                )
            )
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        with path.open("rb") as stream:
            while chunk := stream.read(256 * 1024):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif event["type"] == "lifespan.shutdown":
                    await asyncio.to_thread(self.manager.close)
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            return
        try:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            host = headers.get("host", "").split(":")[0]
            if host not in self.allowed_hosts:
                await self.send_json(send, 400, {"error": "invalid host"})
                return
            path = scope["path"]
            method = scope["method"]
            if path == "/" and method == "GET":
                await self.send_file(send, Path(__file__).with_name("saas_dashboard.html"))
                return
            if path == "/health" and method == "GET":
                await self.send_json(send, 200, {"status": "ok", "productStage": "private-preview"})
                return
            preview = re.fullmatch(r"/v1/previews/([^/]+)/(.+)", path)
            if preview and method == "GET":
                client, job, revision = self.verify_ticket(preview[1])
                await self.send_file(send, self.artifact(client, job, revision, preview[2]))
                return
            authorization = headers.get("authorization", "")
            client = (
                self.store.authenticate(authorization[7:])
                if authorization.startswith("Bearer ")
                else None
            )
            if not client:
                raise PermissionError("authentication required")
            if path == "/v1/jobs" and method == "GET":
                await self.send_json(send, 200, {"jobs": self.store.jobs(client)})
                return
            if path == "/v1/jobs" and method == "POST":
                query = parse_qs(scope.get("query_string", b"").decode())
                width = int(query.get("width", ["2560"])[0])
                height = int(query.get("height", ["1600"])[0])
                mode = query.get("mode", ["auto"])[0]
                if (
                    not 240 <= width <= 8192
                    or not 240 <= height <= 8192
                    or width * height > 32_000_000
                    or mode not in {"auto", "preserve", "reflow"}
                ):
                    raise ValueError("invalid target geometry")
                config = {"width": width, "height": height, "mode": mode}
                job, created = self.store.new_job(client, config, headers.get("idempotency-key"))
                if not created:
                    await self.send_json(send, 200, {"job": job, "idempotentReplay": True})
                    return
                root = self.store.job_root(client, job)
                try:
                    maximum = 100 * 1024 * 1024
                    size = 0
                    with (root / "source.hwt").open("wb") as stream:
                        while True:
                            message = await receive()
                            if message["type"] == "http.disconnect":
                                raise ValueError("upload disconnected")
                            data = message.get("body", b"")
                            size += len(data)
                            if size > maximum:
                                raise OverflowError("upload exceeds 100MB")
                            stream.write(data)
                            if not message.get("more_body"):
                                break
                    if not size:
                        raise ValueError("empty upload")
                    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
                    task = self.manager.submit(client, job, "convert")
                except Exception:
                    self.store.fail_upload(client, job)
                    raise
                await self.send_json(send, 202, {"job": job, "task": task["id"]})
                return
            task_match = re.fullmatch(r"/v1/tasks/([0-9a-f]{32})", path)
            if task_match and method == "GET":
                await self.send_json(send, 200, self.store.task(client, task_match[1]))
                return
            match = re.fullmatch(r"/v1/jobs/([0-9a-f]{32})(?:/(.*))?", path)
            if not match:
                raise KeyError("route not found")
            job, operation = match[1], match[2] or ""
            record = self.store.job(client, job)
            root = self.store.job_root(client, job)
            revision = record["active_revision"]
            if operation == "" and method == "GET":
                await self.send_json(send, 200, record)
                return
            if record["status"] not in {"succeeded", "editing", "matching"}:
                raise ValueError("result is not available")
            if operation == "preview" and method == "GET":
                ticket = self.ticket(client, job, revision)
                await self.send_json(
                    send,
                    200,
                    {
                        "url": "/v1/previews/" + ticket + "/project/preview.html",
                        "expiresIn": 600,
                        "revision": revision,
                    },
                )
                return
            if operation.startswith("files/") and method == "GET":
                await self.send_file(send, self.artifact(client, job, revision, operation[6:]))
                return
            if operation == "layout-request" and method == "GET":
                from multires_app.preview.layout_refinement import export_request

                project = json.loads(
                    (root / "revisions" / str(revision) / "project/project.json").read_text(
                        encoding="utf-8"
                    )
                )
                await self.send_json(
                    send,
                    200,
                    export_request(project, project["devices"][0]["profile"]["id"], "state-000"),
                )
                return
            if operation == "layout-proposal" and method == "POST":
                proposal = json.loads(await self.body(receive, 1024 * 1024))
                task = self.manager.submit(client, job, "apply", proposal)
                await self.send_json(send, 202, {"job": job, "task": task["id"]})
                return
            if operation == "reference-image" and method == "POST":
                data = await self.body(receive, 10 * 1024 * 1024)
                task = self.manager.submit(client, job, "match", data)
                await self.send_json(send, 202, {"job": job, "task": task["id"]})
                return
            rollback = re.fullmatch(r"revisions/(\d+)/activate", operation)
            if rollback and method == "POST":
                self.store.activate_revision(client, job, int(rollback[1]))
                await self.send_json(send, 200, {"revision": int(rollback[1])})
                return
            raise KeyError("route not found")
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
            return
        except PermissionError:
            await self.send_json(send, 401, {"error": "authentication required or preview expired"})
        except KeyError:
            await self.send_json(send, 404, {"error": "not found"})
        except OverflowError:
            await self.send_json(send, 413, {"error": "request exceeds size limit"})
        except ValueError as exc:
            await self.send_json(
                send, 429 if "quota" in str(exc) else 400, {"error": str(exc)[:240]}
            )
        except Exception:
            await self.send_json(
                send, 500, {"error": "operation failed", "requestId": secrets.token_hex(8)}
            )


def create_app():
    return SaaSApp(
        ServiceStore(Path(os.environ.get("MULTIRES_SERVICE_ROOT", ".saas-data"))),
        allowed_hosts=os.environ.get("MULTIRES_ALLOWED_HOSTS", "localhost,127.0.0.1").split(","),
    )
