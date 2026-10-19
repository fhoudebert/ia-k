"""Model servers of the current computer (config/servers.toml) and a minimal
client for their OpenAI-compatible API (llama-server, LM Studio)."""
from __future__ import annotations

import json
import os
import re
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
    return Host(name, chat=h.get("chat") or None, embedding=h.get("embedding") or None,
                vision=h.get("vision") or None, api_key=h.get("api_key", ""))


HEADER = """# IA-K - where to find the llama.cpp servers on each computer.
# Written by IA-K's settings screen; see config/servers.example.toml for the
# format. A [host.*] section applies when `match` contains the computer name.
"""


def _toml_value(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dump_hosts(hosts: dict) -> str:
    out = [HEADER]
    for name, h in hosts.items():
        out.append(f"\n[host.{name}]")
        for key in ("match", "chat", "embedding", "vision", "api_key"):
            if h.get(key) not in (None, "", []):
                out.append(f"{key} = {_toml_value(h[key])}")
    return "\n".join(out) + "\n"


def save_host(config: str | Path, chat: str | None, embedding: str | None,
              vision: str | None, api_key: str | None = None,
              name: str | None = None) -> Host:
    """Write the servers of this computer into config (other computers'
    sections are kept; the previous file is saved as .bak). api_key None
    keeps the stored key, "" removes it."""
    path = Path(config)
    hosts: dict = {}
    if path.exists():
        with open(path, "rb") as fh:
            hosts = tomllib.load(fh).get("host", {})
    me = computer_name()
    if not name:
        name = next((k for k, v in hosts.items()
                     if me.lower() in [str(m).lower() for m in v.get("match", [])]), None)
    if not name:
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", me).strip("-").lower() or "this-computer"
    section = dict(hosts.get(name, {}))
    if name != "default":
        match = list(section.get("match", []))
        if me.lower() not in [m.lower() for m in match]:
            match.append(me)
        section["match"] = match
    for key, value in (("chat", chat), ("embedding", embedding), ("vision", vision)):
        section[key] = (value or "").strip()
    if api_key is not None:
        section["api_key"] = api_key
    hosts[name] = section
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump_hosts(hosts), encoding="utf-8")
    os.replace(tmp, path)
    return load_host(path, name)


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

    def chat_stream(self, messages: list[dict], max_tokens: int = 700,
                    temperature: float = 0.2, thinking: bool = False):
        """Yield the answer piece by piece (server-sent events)."""
        payload = {"model": "", "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature, "cache_prompt": True, "stream": True,
                   "chat_template_kwargs": {"enable_thinking": thinking}}
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.v1 + "/chat/completions",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            raise ServerError(f"chat: HTTP {exc.code} {body}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ServerError(f"chat: unreachable ({exc})") from exc
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    choice = (json.loads(data).get("choices") or [{}])[0]
                except ValueError:
                    continue
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece

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
