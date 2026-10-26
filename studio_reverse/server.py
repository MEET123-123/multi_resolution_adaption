"""Independent multi-device lockscreen preview built on the existing XML Runtime.
Run from the repository root: .venv\\Scripts\\python.exe studio_reverse\\server.py
"""
from __future__ import annotations
import json, mimetypes, secrets, sys, threading, uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from multires_app.preview.live_preview import LiveSession

BASE = Path(__file__).resolve().parent
WORK = BASE / "runtime"
WORK.mkdir(parents=True, exist_ok=True)
TOKEN = secrets.token_urlsafe(24)
state = {"session": None}
lock = threading.RLock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(fmt % args)
    def respond(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path in {"/", "/index.html"}:
            data = (BASE / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN).encode("utf-8")
            typ = "text/html; charset=utf-8"
        elif path == "/app.js":
            data = (BASE / "app.js").read_bytes()
            typ = "application/javascript; charset=utf-8"
        elif path.startswith("/assets/") and state["session"]:
            s = state["session"]
            file = (s.output / path.lstrip("/")).resolve()
            if not file.is_relative_to(s.output) or not file.is_file():
                self.send_error(404); return
            data = file.read_bytes()
            typ = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        if self.headers.get("X-Preview-Token") != TOKEN:
            self.respond(403, {"error": "invalid token"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 300 * 1024 * 1024:
                self.respond(413, {"error": "请求为空或超过 300MB"}); return
            endpoint = urlsplit(self.path).path
            if endpoint == "/api/import":
                filename = Path(unquote(self.headers.get("X-Filename", "theme.hwt"))).name
                if not filename.lower().endswith((".hwt", ".zip")):
                    self.respond(400, {"error": "仅支持 HWT/ZIP"}); return
                source = WORK / ("upload-" + uuid.uuid4().hex[:8] + "-" + filename)
                source.write_bytes(self.rfile.read(length))
                try:
                    session = LiveSession(source, WORK / uuid.uuid4().hex[:8], filename=filename)
                    plan = session.plan()
                except Exception:
                    source.unlink(missing_ok=True)
                    raise
                with lock: state["session"] = session
                self.respond(200, plan); return
            if length > 1024 * 1024:
                self.respond(413, {"error": "JSON too large"}); return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                self.respond(400, {"error": "JSON object expected"}); return
            with lock:
                session = state["session"]
                if not session:
                    self.respond(409, {"error": "请先导入 HWT"}); return
                if endpoint == "/api/plan": result = session.plan()
                elif endpoint == "/api/gesture": result = session.gesture(body)
                elif endpoint == "/api/press": result = session.press(body)
                elif endpoint == "/api/drag": result = session.drag_phase(body)
                elif endpoint == "/api/reset":
                    session.reset(); result = session.plan()
                else:
                    self.send_error(404); return
            self.respond(200, result)
        except Exception as exc:
            self.respond(400, {"error": str(exc)[:500]})

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Theme Studio research: isolated multi-device preview")
    parser.add_argument("--port", type=int, default=8796)
    parser.add_argument("--theme", type=Path)
    args = parser.parse_args()
    if args.theme:
        state["session"] = LiveSession(args.theme, WORK / uuid.uuid4().hex[:8])
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Local multi-device preview: http://127.0.0.1:{args.port}/", flush=True)
    try: server.serve_forever()
    finally: server.server_close()

if __name__ == "__main__":
    main()
