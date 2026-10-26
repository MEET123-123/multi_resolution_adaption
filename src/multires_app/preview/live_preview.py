"""HTTP adapter for the live theme preview.

Session/gesture logic lives in live_session; geometry and animation conversion
have independent modules. Historical helper imports remain available here.
"""

from __future__ import annotations
import json
import mimetypes
import secrets
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from multires_app.preview.live_animation import ANIM_TAGS, frame_list, tracks_for
from multires_app.preview.live_geometry import (
    canvas_size,
    inside,
    pick_owner,
    rect_of,
    slider_geometry,
    subtree_ids,
)
from multires_app.preview.live_session import GESTURES, LiveSession


def serve_live(
    source=None, port=8767, output=Path("preview-live"), allow_import=True, vlm_config=None
):
    source = Path(source).resolve() if source else None
    output = Path(output).resolve()
    token = secrets.token_urlsafe(32)
    state = {"session": LiveSession(source, output / uuid.uuid4().hex[:8]) if source else None}
    from multires_app.preview.reconstruction_live import ReconstructionJobs

    reconstruction = ReconstructionJobs(output, vlm_config)
    guard = threading.Lock()
    page = Path(__file__).with_name("live_preview.html").read_text(encoding="utf-8")
    script = Path(__file__).with_name("live_preview.js").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def send_json(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path in {"/", "/index.html"}:
                config = json.dumps({"token": token, "api": "/api/", "import": allow_import})
                data = (
                    page.replace("__LIVE_CONFIG__", config).replace("__SCRIPT__", script).encode()
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith("/assets/"):
                session = state["session"]
                if session is None:
                    self.send_error(404)
                    return
                target = (session.output / path.lstrip("/")).resolve()
                if not target.is_relative_to(session.output) or not target.is_file():
                    self.send_error(404)
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith("/reconstruction/"):
                target = reconstruction.artifact(path)
                if target is None:
                    self.send_error(404)
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
                return
            self.send_error(404)

        do_HEAD = do_GET

        def do_POST(self):
            if self.headers.get("X-Live-Token") != token:
                self.send_json(403, {"error": "invalid token"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_json(400, {"error": "invalid length"})
                return
            operation = urlsplit(self.path).path
            if operation == "/api/import":
                if not allow_import:
                    self.send_json(403, {"error": "导入已禁用"})
                    return
                if not 0 < length <= 300 * 1024 * 1024:
                    self.send_json(413, {"error": "文件为空或超过 300MB"})
                    return
                name = Path(unquote(self.headers.get("X-Filename", "theme.hwt"))).name
                if not name.lower().endswith((".hwt", ".zip")):
                    self.send_json(400, {"error": "只支持 .hwt / .zip"})
                    return
                target = output / "imports" / f"{uuid.uuid4().hex[:8]}-{name}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.rfile.read(length))
                try:
                    with guard:
                        state["session"] = LiveSession(
                            target, output / uuid.uuid4().hex[:8], filename=name
                        )
                        state.pop("original_session", None)
                        payload = state["session"].plan()
                except Exception as exc:
                    target.unlink(missing_ok=True)
                    self.send_json(400, {"error": f"主题解析失败：{exc}"})
                    return
                self.send_json(200, payload)
                return
            if length > 1024 * 1024:
                self.send_json(413, {"error": "request too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self.send_json(400, {"error": "invalid json"})
                return
            if not isinstance(body, dict):
                self.send_json(400, {"error": "请求必须为 JSON 对象"})
                return
            session = state["session"]
            if operation.startswith("/api/reconstruction/"):
                try:
                    with guard:
                        action = operation.rsplit("/", 1)[-1]
                        if action == "inspect":
                            result = reconstruction.inspect(
                                body.get("project", ""), state.get("original_session", session)
                            )
                        elif action == "analyse":
                            result = reconstruction.analyse(state.get("original_session", session))
                        elif action == "start":
                            result = reconstruction.start(
                                body, state.get("original_session", session)
                            )
                        elif action == "status":
                            result = reconstruction.status(body.get("id", ""))
                        elif action == "activate":
                            result = reconstruction.activate(body.get("id", ""))
                            reconstruction.inspect(
                                result["project"], state.get("original_session", session)
                            )
                            candidate = LiveSession(
                                Path(result["package"]), output / uuid.uuid4().hex[:8]
                            )
                            candidate.width = result["viewport"]["width"]
                            candidate.height = result["viewport"]["height"]
                            candidate.reset()
                            if session is not None:
                                state.setdefault("original_session", session)
                            state["session"] = candidate
                            result = candidate.plan()
                        elif action == "restore":
                            original = state.pop("original_session", None)
                            if original is None:
                                raise ValueError("当前没有待返回的原主题")
                            state["session"] = original
                            result = original.plan()
                        else:
                            raise ValueError("未知重构操作")
                    self.send_json(200, result)
                except (ValueError, KeyError, OSError) as exc:
                    self.send_json(400, {"error": str(exc)[:700]})
                except Exception:
                    self.send_json(500, {"error": "布局重构请求失败，请查看任务报告"})
                return
            if session is None:
                self.send_json(409, {"error": "尚未导入主题"})
                return
            try:
                with guard:
                    if operation == "/api/gesture":
                        result = session.gesture(body)
                    elif operation == "/api/press":
                        result = session.press(body)
                    elif operation == "/api/drag":
                        result = session.drag_phase(body)
                    elif operation == "/api/plan":
                        result = session.plan()
                    elif operation == "/api/reset":
                        session.reset()
                        result = session.plan()
                    else:
                        self.send_error(404)
                        return
                self.send_json(200, result)
            except (ValueError, KeyError) as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(500, {"error": "预览执行失败：" + str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{server.server_port}/",
                "theme": str(source) if source else None,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
