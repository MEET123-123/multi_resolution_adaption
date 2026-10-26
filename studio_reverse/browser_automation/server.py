"""Standalone browser control server for Theme Studio research (localhost only)."""
from __future__ import annotations
import base64
import json
import queue
import secrets
import threading
import uuid
from datetime import datetime
from concurrent.futures import Future
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parent
PROFILE = BASE / ".browser-profile"
CAPTURES = BASE / "captures"

def allowed_url(url):
    try:
        u = urlsplit(url)
        return u.scheme in ("http", "https") and bool(u.hostname) and not u.username and not u.password
    except (TypeError, ValueError):
        return False

def scrub(url):
    try:
        u = urlsplit(url)
        if u.scheme not in ("http", "https", "ws", "wss"):
            return "[non-web]"
        return f"{u.scheme}://{u.hostname}{(':' + str(u.port)) if u.port else ''}{u.path}"
    except (TypeError, ValueError):
        return "[invalid]"

class Browser:
    """All Playwright objects stay in one worker thread."""
    def __init__(self):
        self.jobs = queue.Queue()
        self.context = None
        self.page = None
        self.events = []
        threading.Thread(target=self._work, name="browser-worker", daemon=True).start()

    def call(self, action, body=None):
        result = Future()
        self.jobs.put((action, body or {}, result))
        return result.result(timeout=90)

    def _work(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                self.playwright = playwright
                while True:
                    try:
                        action, body, result = self.jobs.get(timeout=0.25)
                    except queue.Empty:
                        if self.context is not None:
                            try: self.current().wait_for_timeout(100)
                            except Exception: pass
                        continue
                    try: result.set_result(self.run(action, body))
                    except Exception as error: result.set_exception(error)
        except Exception as error:
            while True:
                _, _, result = self.jobs.get()
                result.set_exception(RuntimeError("浏览器启动失败：" + str(error)))

    def current(self):
        if not self.context:
            raise ValueError("请先启动浏览器")
        pages = [p for p in self.context.pages if not p.is_closed()]
        if self.page not in pages:
            self.page = pages[-1] if pages else self.context.new_page()
        return self.page

    def note(self, kind, request, status=None):
        if request.resource_type not in ("fetch", "xhr", "document", "websocket"): return
        self.events.append({"time": datetime.now().isoformat(timespec="seconds"),
                            "event": kind, "method": request.method,
                            "type": request.resource_type, "url": scrub(request.url),
                            "status": status})
        self.events = self.events[-300:]

    def state(self):
        if not self.context: return {"running": False}
        page = self.current()
        return {"running": True, "title": page.title()[:160], "url": scrub(page.url),
                "tabs": len(self.context.pages), "requests": len(self.events)}

    def launch(self, body):
        url = body.get("url") or "https://themestudio.cloud.huawei.com.cn/"
        if not allowed_url(url): raise ValueError("只允许 HTTP(S) 网页")
        if not self.context:
            channel = body.get("channel", "msedge")
            if channel not in ("msedge", "chrome", "chromium"):
                raise ValueError("仅支持 msedge / chrome / chromium")
            PROFILE.mkdir(parents=True, exist_ok=True)
            kwargs = {"user_data_dir": str(PROFILE), "headless": False,
                      "viewport": {"width": 1440, "height": 900}}
            if channel != "chromium": kwargs["channel"] = channel
            self.context = self.playwright.chromium.launch_persistent_context(**kwargs)
            self.context.on("page", lambda new: setattr(self, "page", new))
            self.context.on("request", lambda req: self.note("request", req))
            self.context.on("response", lambda res: self.note("response", res.request, res.status))
        self.current().goto(url, wait_until="domcontentloaded", timeout=40000)
        return self.state()

    def locator(self, body):
        page = self.current()
        how = body.get("by", "role")
        text = str(body.get("query") or "")[:300]
        if not text: raise ValueError("请输入元素名称或选择器")
        exact = bool(body.get("exact", True))
        if how == "role":
            role = body.get("role", "button")
            if role not in ("button", "link", "tab", "textbox", "combobox", "checkbox", "radio", "menuitem", "option"):
                raise ValueError("无效的控件角色")
            loc = page.get_by_role(role, name=text, exact=exact)
        elif how == "text": loc = page.get_by_text(text, exact=exact)
        elif how == "label": loc = page.get_by_label(text, exact=exact)
        elif how == "placeholder": loc = page.get_by_placeholder(text, exact=exact)
        elif how == "css": loc = page.locator(text)
        else: raise ValueError("未知定位方式")
        index = int(body.get("nth", 0))
        if not 0 <= index <= 30: raise ValueError("元素序号必须为 0～30")
        return loc.nth(index)

    def action(self, kind, body):
        page = self.current()
        if kind == "navigate":
            url = body.get("url", "")
            if not allowed_url(url): raise ValueError("只允许 HTTP(S) 网页")
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            return self.state()
        if kind in ("click", "fill", "select"):
            loc = self.locator(body)
            if kind == "click": loc.click(timeout=15000)
            elif kind == "fill": loc.fill(str(body.get("value", ""))[:1000], timeout=15000)
            else: loc.select_option(str(body.get("value", ""))[:200], timeout=15000)
            return {"ok": True, "page": self.state()}
        if kind == "scroll":
            page.mouse.wheel(0, max(-2000, min(2000, float(body.get("y", 500)))))
            return {"ok": True}
        if kind == "wait":
            ms = max(0, min(10000, int(float(body.get("seconds", 1))*1000)))
            page.wait_for_timeout(ms)
            return self.state()
        if kind == "inspect":
            items = page.locator("button,a,[role=button],[role=tab],input:not([type=hidden]),select").evaluate_all(
                """els=>els.filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'}).slice(0,120).map(e=>({tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,90),placeholder:(e.getAttribute('placeholder')||'').slice(0,60),type:e.getAttribute('type')||'',id:e.id||''}))""")
            return {"page": self.state(), "elements": items,
                    "frames": [{"name": f.name[:80], "url": scrub(f.url)} for f in page.frames[:20]]}
        if kind == "screenshot":
            CAPTURES.mkdir(parents=True, exist_ok=True)
            data = page.screenshot(type="png", full_page=False, timeout=15000)
            path = CAPTURES / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:5] + ".png")
            path.write_bytes(data)
            return {"image": base64.b64encode(data).decode("ascii"), "file": str(path.relative_to(BASE)), "page": self.state()}
        raise ValueError("未知操作：" + kind)

    def run(self, kind, body):
        if kind == "launch": return self.launch(body)
        if kind == "status": return self.state()
        if kind == "stop":
            if self.context: self.context.close()
            self.context = self.page = None
            return {"running": False}
        if kind == "network": return {"events": self.events[-200:]}
        if kind == "export":
            CAPTURES.mkdir(parents=True, exist_ok=True)
            path = CAPTURES / ("network-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
            path.write_text(json.dumps(self.events, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"file": str(path.relative_to(BASE)), "count": len(self.events)}
        if kind == "playbook":
            steps = body.get("steps")
            if not isinstance(steps, list) or len(steps) > 30: raise ValueError("最多 30 步")
            done = []
            for i, step in enumerate(steps):
                name = step.get("action") if isinstance(step, dict) else None
                if name not in ("navigate", "click", "fill", "select", "scroll", "wait", "inspect", "screenshot"):
                    raise ValueError("步骤包含不允许的操作")
                try: self.action(name, step); done.append({"step": i+1, "action": name, "ok": True})
                except Exception as e:
                    done.append({"step": i+1, "action": name, "ok": False, "error": str(e)[:250]})
                    break
            return {"steps": done, "page": self.state()}
        return self.action(kind, body)

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8797)
    args = parser.parse_args()
    token = secrets.token_urlsafe(30)
    browser = Browser()
    html = (BASE / "index.html").read_text(encoding="utf-8").replace("__LOCAL_TOKEN__", token).encode()
    script = (BASE / "controller.js").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def respond(self, status, data):
            out = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(out)
        def do_GET(self):
            if self.path == "/": data, mime = html, "text/html; charset=utf-8"
            elif self.path == "/controller.js": data, mime = script, "application/javascript"
            else: self.send_error(404); return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            if self.headers.get("X-Browser-Token") != token:
                self.respond(403, {"error": "invalid token"}); return
            origin = self.headers.get("Origin")
            if origin and origin != "http://127.0.0.1:" + str(self.server.server_port):
                self.respond(403, {"error": "invalid origin"}); return
            if not self.path.startswith("/api/"):
                self.send_error(404); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 70000: raise ValueError("请求过大")
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict): raise ValueError("必须是 JSON 对象")
                kind = self.path[5:]
                if kind not in ("launch", "status", "navigate", "click", "fill", "select",
                                "scroll", "wait", "inspect", "screenshot", "network", "export", "stop", "playbook"):
                    self.send_error(404); return
                self.respond(200, browser.call(kind, payload))
            except Exception as e:
                self.respond(400, {"error": str(e)[:500]})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("浏览器操作台：http://127.0.0.1:" + str(args.port) + "/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        try: browser.call("stop")
        except Exception: pass
        server.server_close()

if __name__ == "__main__":
    main()
