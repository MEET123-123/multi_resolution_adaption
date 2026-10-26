"""OpenAI-compatible chat transport, callable plugins and explicit offline mode."""

import base64
import io
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from PIL import Image
from .contract import PROMPT
from .templates import propose


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _open(request, timeout):
    handlers = [NoRedirect()]
    if urlsplit(request.full_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        handlers.append(ProxyHandler({}))
    return build_opener(*handlers).open(request, timeout=timeout)


class OpenAICompatible:
    def __init__(self, config):
        allowed = {
            "base_url",
            "model",
            "api_key_env",
            "timeout",
            "max_tokens",
            "json_mode",
            "max_image_edge",
        }
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("模型配置字段无效（密钥请使用环境变量）")
        self.config = dict(config)
        url = config.get("base_url", "").rstrip("/")
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("模型 base_url 无效")
        if not isinstance(config.get("model"), str) or not config["model"].strip():
            raise ValueError("请配置模型名称")
        for key, default, low, high in [
            ("timeout", 45, 1, 120),
            ("max_tokens", 4096, 256, 8192),
            ("max_image_edge", 1024, 256, 1536),
        ]:
            v = config.get(key, default)
            if type(v) is not int or not low <= v <= high:
                raise ValueError("模型配置范围无效：" + key)
            self.config[key] = v
        if type(config.get("json_mode", False)) is not bool:
            raise ValueError("json_mode 必须为布尔值")
        env = config.get("api_key_env")
        if env is not None and (not isinstance(env, str) or not env.isidentifier()):
            raise ValueError("密钥环境变量名称无效")
        self.url = url + "/chat/completions"
        self.name = "openai-compatible"

    def __call__(self, request, images, feedback):
        text = {"request": request, "feedback": feedback}
        content = [{"type": "text", "text": json.dumps(text, ensure_ascii=False, allow_nan=False)}]
        for label, path in images[:6]:
            path = Path(path)
            with Image.open(path) as im:
                if im.width * im.height > 20_000_000:
                    raise ValueError("模型图片超出像素预算")
                im = im.convert("RGB")
                im.thumbnail((self.config["max_image_edge"],) * 2)
                buffer = io.BytesIO()
                im.save(buffer, format="JPEG", quality=85)
            content.extend(
                [
                    {"type": "text", "text": label},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(buffer.getvalue()).decode()
                        },
                    },
                ]
            )
        payload = {
            "model": self.config["model"],
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": content},
            ],
            "max_tokens": self.config["max_tokens"],
            "temperature": 0.2,
        }
        if self.config.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        env = self.config.get("api_key_env")
        if env:
            key = os.environ.get(env)
            if not key:
                raise ValueError("模型密钥环境变量未设置：" + env)
            headers["Authorization"] = "Bearer " + key
        # No HTTP diagnostics/body/key are stored in artifacts or shown to the browser.
        try:
            with _open(
                Request(self.url, json.dumps(payload).encode(), headers),
                timeout=self.config["timeout"],
            ) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("模型响应超过 2MB")
            result = json.loads(raw)
            choice = result["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("模型输出被截断")
            answer = choice["message"]["content"]
            if not isinstance(answer, str):
                raise ValueError("模型需返回 JSON 文本")
            answer = answer.strip()
            if answer.startswith("```json") and answer.endswith("```"):
                answer = answer[7:-3].strip()
            return json.loads(answer)
        except (OSError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise ValueError(
                "模型接口调用失败（" + type(exc).__name__ + "），请检查服务、模型名和兼容接口配置"
            ) from None


class MockProvider:
    name = "mock-rule-provider"

    def __init__(self, template=None):
        self.template = template

    def __call__(self, request, images, feedback):
        if feedback:
            return {
                "schema": "layout-reconstruction-advice/1",
                "inputDigest": request["inputDigest"],
                "candidates": [],
                "stop": True,
            }
        if self.template:
            return propose(request, self.template)
        result = propose(request)
        for template in ("hero-dock", "card-grid"):
            if template != result["candidates"][0]["template"]:
                result["candidates"].extend(propose(request, template)["candidates"])
        if len(result["candidates"]) < 3:
            result["candidates"].extend(propose(request, "hero-support")["candidates"])
        return result
