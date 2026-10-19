#!/usr/bin/env python3
"""IA-K smoke test for a running llama-server (standard library only).

Measures what matters for document chat on an iGPU:
  * prompt processing speed (prefill) on a long prompt, the main source of
    waiting time before the first word of an answer;
  * generation speed;
  * an image question when the server was started with a vision projector;
  * embeddings, when an embedding server is given.

On Linux with the amdgpu driver it also prints the iGPU memory in use
(VRAM carve-out + GTT), read from /sys.

Examples:
  python scripts/smoke_test.py
  python scripts/smoke_test.py --image assets/ia-k.png --prefill 6000
  python scripts/smoke_test.py --embed-url http://127.0.0.1:8081
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import math
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FILLER = (
    "Le registre de la bibliothèque mentionne l'achat de trois volumes reliés, "
    "d'un atlas et de plusieurs cartes marines au cours de l'année. "
)


def call(url: str, payload: dict | None = None, timeout: float = 900.0) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def gpu_memory() -> str:
    """iGPU memory in use (Linux amdgpu only), empty string elsewhere."""
    parts = []
    for dev in sorted(glob.glob("/sys/class/drm/card*/device")):
        vals = {}
        for key in ("vram", "gtt"):
            used = os.path.join(dev, f"mem_info_{key}_used")
            total = os.path.join(dev, f"mem_info_{key}_total")
            try:
                with open(used) as fu, open(total) as ft:
                    vals[key] = (int(fu.read()) / 2**30, int(ft.read()) / 2**30)
            except (OSError, ValueError):
                pass
        if vals:
            parts.append(", ".join(f"{k.upper()} {u:.1f}/{t:.1f} GiB"
                                   for k, (u, t) in vals.items()))
    return " | ".join(parts)


def show_timings(label: str, resp: dict, wall: float) -> None:
    t = resp.get("timings") or {}
    text = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "")
    print(f"\n== {label} ==")
    if t:
        print(f"  prompt : {t.get('prompt_n', '?'):>6} tokens  "
              f"{t.get('prompt_per_second', 0):8.1f} tok/s  "
              f"({t.get('prompt_ms', 0) / 1000:.1f} s)")
        print(f"  answer : {t.get('predicted_n', '?'):>6} tokens  "
              f"{t.get('predicted_per_second', 0):8.1f} tok/s")
    print(f"  wall   : {wall:.1f} s")
    print("  reply  : " + " ".join(text.split())[:300])


def chat(base: str, model: str, content, max_tokens: int) -> tuple[dict, float]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "cache_prompt": False,  # measure a cold prompt every time
    }
    t0 = time.perf_counter()
    resp = call(f"{base}/v1/chat/completions", payload)
    return resp, time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8080",
                    help="chat server (default %(default)s)")
    ap.add_argument("--prefill", type=int, default=4000,
                    help="approximate prompt length in tokens for the "
                         "long-prompt test, 0 to skip (default %(default)s)")
    ap.add_argument("--image", help="image file for the vision test")
    ap.add_argument("--embed-url", help="embedding server, e.g. http://127.0.0.1:8081")
    ap.add_argument("--max-tokens", type=int, default=200)
    args = ap.parse_args()
    base = args.url.rstrip("/")
    failures = 0

    mem = gpu_memory()
    if mem:
        print(f"iGPU memory before: {mem}")

    try:
        health = call(f"{base}/health", timeout=10)
        models = call(f"{base}/v1/models", timeout=10)
    except (urllib.error.URLError, OSError) as exc:
        print(f"server unreachable at {base}: {exc}")
        return 2
    model = (models.get("data") or [{}])[0].get("id", "")
    print(f"server {base}: health={health.get('status', health)} model={model}")

    try:
        resp, wall = chat(base, model,
                          "En trois phrases, explique ce qu'est la recherche "
                          "hybride (mots-clés + sens) dans une base documentaire.",
                          args.max_tokens)
        show_timings("short question", resp, wall)
    except (urllib.error.URLError, OSError) as exc:
        print(f"short question failed: {exc}")
        failures += 1

    if args.prefill > 0:
        # ~40 tokens per numbered filler sentence (rough; the server reports
        # the exact count in the timings below)
        n = max(1, args.prefill // 40)
        doc = "".join(f"[S{i + 1}] {FILLER}" for i in range(n))
        prompt = (f"Passages :\n{doc}\n\nQuestion : combien de volumes reliés "
                  "sont mentionnés ? Réponds en une phrase en citant un passage.")
        try:
            resp, wall = chat(base, model, prompt, 64)
            show_timings(f"long prompt (~{args.prefill} tokens)", resp, wall)
        except (urllib.error.URLError, OSError) as exc:
            print(f"long prompt failed (context too small or out of memory?): {exc}")
            failures += 1

    if args.image:
        mime = mimetypes.guess_type(args.image)[0] or "image/png"
        with open(args.image, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        content = [
            {"type": "text", "text": "Décris cette image et transcris le texte "
                                     "visible, s'il y en a."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
        try:
            resp, wall = chat(base, model, content, args.max_tokens)
            show_timings(f"image {os.path.basename(args.image)}", resp, wall)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            print(f"image test failed ({exc.code}): {body}")
            print("  -> was the server started with a vision projector (VISION=1)?")
            failures += 1
        except (urllib.error.URLError, OSError) as exc:
            print(f"image test failed: {exc}")
            failures += 1

    if args.embed_url:
        ebase = args.embed_url.rstrip("/")
        texts = [
            "task: search result | query: achat de livres reliés",
            "title: none | text: " + FILLER,
            "title: none | text: La recette demande deux oeufs et du beurre.",
        ]
        try:
            t0 = time.perf_counter()
            resp = call(f"{ebase}/v1/embeddings",
                        {"model": "embeddinggemma", "input": texts})
            wall = time.perf_counter() - t0
            vecs = [d["embedding"] for d in resp["data"]]

            def cos(a, b):
                return sum(x * y for x, y in zip(a, b)) / (
                    math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

            print("\n== embeddings ==")
            print(f"  dimension : {len(vecs[0])}   ({wall * 1000:.0f} ms for 3 texts)")
            print(f"  query/relevant   : {cos(vecs[0], vecs[1]):.3f}")
            print(f"  query/irrelevant : {cos(vecs[0], vecs[2]):.3f}  (should be lower)")
        except (urllib.error.URLError, OSError, KeyError, IndexError) as exc:
            print(f"embedding test failed: {exc}")
            failures += 1

    mem = gpu_memory()
    if mem:
        print(f"\niGPU memory after: {mem}")
    else:
        print("\n(iGPU memory: read it in the server log lines 'model buffer size' /"
              " 'KV buffer size', or in the Task Manager > Performance > GPU)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
