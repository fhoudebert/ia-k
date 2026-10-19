"""Model servers of the current computer (config/servers.toml) and a minimal
client for their OpenAI-compatible API (llama-server, LM Studio)."""
from __future__ import annotations

import json
import os
import socket
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class ServerError(RuntimeError):
    pass


@dataclass
class Host:
    name: str
    chat: str | None = None
    embedding: str | None = None
    vision: str | None = None
    api_key: str = ""


def computer_name() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def load_host(config: str | Path, name: str | None = None) -> Host:
    """The [host.*] section for this computer: chosen by `name`, else the first
    whose `match` contains the computer name, else [host.default]."""
    path = Path(config)
    if not path.exists():
        return Host("default", chat="http://127.0.0.1:8080/v1",
                    embedding="http://127.0.0.1:8081/v1")
    with open(path, "rb") as fh:
        hosts = tomllib.load(fh).get("host", {})
    if name and name not in hosts:
        raise ServerError(f"no [host.{name}] in {path}")
    if not name:
        me = computer_name().lower()
        name = next((k for k, v in hosts.items()
                     if me in [str(m).lower() for m in v.get("match", [])]), "default")
    h = hosts.get(name, {})
    return Host(name, chat=h.get("chat"), embedding=h.get("embedding"),
                vision=h.get("vision"), api_key=h.get("api_key", ""))


def is_local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


class Client:
    """OpenAI-compatible endpoint, e.g. http://127.0.0.1:8080/v1."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 600.0):
        url = base_url.rstrip("/")
        self.v1 = url if url.endswith("/v1") else url + "/v1"
        self.root = self.v1[: -len("/v1")]
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, url: str, payload: dict | None = None, timeout: float | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            raise ServerError(f"{url}: HTTP {exc.code} {body}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ServerError(f"{url}: unreachable ({exc})") from exc

    def props(self) -> dict:
        """llama-server properties: context size, vision support. {} elsewhere."""
        try:
            p = self._call(self.root + "/props", timeout=10)
        except ServerError:
            return {}
        return {"n_ctx": (p.get("default_generation_settings") or {}).get("n_ctx"),
                "vision": (p.get("modalities") or {}).get("vision"),
                "model_path": p.get("model_path")}

    def model(self) -> str:
        data = self._call(self.v1 + "/models", timeout=10).get("data") or [{}]
        return data[0].get("id", "")

    def chat(self, messages: list[dict], max_tokens: int = 700, temperature: float = 0.2,
             thinking: bool = False) -> str:
        payload = {
            "model": "",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        data = self._call(self.v1 + "/chat/completions", payload)
        try:
            return data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ServerError(f"unexpected answer: {str(data)[:200]}") from exc
