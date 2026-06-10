"""
title: Auto Vision
author: vera
version: 0.1.0
description: Deterministically routes any attached image to the configured vision endpoint (e.g. Qwen3-VL) and injects the description into the prompt, so text-only Vera always "sees" attached images (no reliance on the model choosing to call a tool). Strips the image so the text model never receives unparseable image data.
required_open_webui_version: 0.5.0
"""

import json
import requests
from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        vision_endpoint: str = Field(
            default="http://localhost:8082/v1/chat/completions",
            description="OpenAI-compatible vision endpoint (e.g. an mlx-vlm server).",
        )
        vision_model: str = Field(default="mlx-community/Qwen3-VL-8B-Instruct-4bit")
        max_tokens: int = Field(default=700)
        timeout: int = Field(default=120)

    def __init__(self):
        self.valves = self.Valves()

    def _collect_images(self, obj, out):
        """Recursively find image data URLs anywhere in the body."""
        if isinstance(obj, str):
            if obj.startswith("data:image"):
                out.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                self._collect_images(v, out)
        elif isinstance(obj, list):
            for v in obj:
                self._collect_images(v, out)

    def _images_and_text(self, body: dict):
        msgs = body.get("messages") or []
        last = next((m for m in reversed(msgs) if m.get("role") == "user"), None)
        text = ""
        if last is not None:
            c = last.get("content")
            if isinstance(c, str):
                text = c
            elif isinstance(c, list):
                text = " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
        images: list = []
        # Look in files + the last user message (recursively, to catch any shape).
        self._collect_images(body.get("files"), images)
        if last is not None:
            self._collect_images(last.get("content"), images)
        # de-dupe preserving order
        seen = set(); images = [u for u in images if not (u in seen or seen.add(u))]
        return images, text.strip(), last

    def _describe(self, image_url: str, question: str) -> str:
        payload = {
            "model": self.valves.vision_model,
            "max_tokens": self.valves.max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": (question or "Describe this image.") +
                    "\n\nDescribe the image thoroughly, including any visible text, layout, and notable details."},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}],
        }
        r = requests.post(self.valves.vision_endpoint, json=payload, timeout=self.valves.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def inlet(self, body: dict, __event_emitter__=None) -> dict:
        images, text, last = self._images_and_text(body)
        if not images or last is None:
            return body

        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        await emit(f"Looking at {len(images)} image(s) with the vision model…")
        notes = []
        for i, u in enumerate(images, 1):
            try:
                notes.append(self._describe(u, text))
            except Exception as e:
                notes.append(f"(vision node unreachable — its host may be off: {e})")
        await emit("Image analyzed", True)

        block = "\n\n".join(
            f"[Vision of attached image {i}]:\n{n}" for i, n in enumerate(notes, 1)
        )
        last["content"] = (f"{text}\n\n{block}" if text else block).strip()
        # Strip images so the text model never receives unparseable image data.
        body["files"] = [f for f in (body.get("files") or []) if not (isinstance(f, dict) and f.get("type") == "image")]
        return body
