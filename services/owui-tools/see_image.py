"""
title: Vision
author: vera
version: 0.1.0
description: Lets text-only Vera "see" an attached image by routing it to a vision model at the configured endpoint (e.g. Qwen3-VL via mlx-vlm). Vera calls this automatically whenever the user attaches an image and the request depends on its visual content.
"""

import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vision_endpoint: str = Field(
            default="http://localhost:8082/v1/chat/completions",
            description="OpenAI-compatible vision endpoint (e.g. an mlx-vlm server).",
        )
        vision_model: str = Field(
            default="mlx-community/Qwen3-VL-8B-Instruct-4bit",
            description="Vision model id served at the endpoint.",
        )
        max_tokens: int = Field(default=512, description="Max tokens for the description.")
        timeout: int = Field(default=120, description="Request timeout (seconds).")

    def __init__(self):
        self.valves = self.Valves()

    async def see_image(
        self,
        query: str = "",
        __messages__: list = None,
        __event_emitter__=None,
    ) -> str:
        """
        Look at the image the user attached and answer a question about it. Call this
        whenever the user has attached an image (photo, screenshot, diagram, chart, or
        document page) and their request depends on its visual content.

        :param query: What to determine about the image — pass the user's question, or a
            request to describe it if they didn't ask anything specific.
        """

        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "status", "data": {"description": desc, "done": done}}
                )

        # Find the most recent image in the conversation (rides inline in message content
        # as an image_url data URL — OWUI does not strip it for text-only models).
        data_url = None
        for msg in reversed(__messages__ or []):
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url")
                        if url:
                            data_url = url
                            break
            if data_url:
                break

        if not data_url:
            await emit("No image found", True)
            return (
                "No image is attached to this message, so there is nothing to look at. "
                "Ask the user to attach an image."
            )

        await emit("Looking at the image…")
        payload = {
            "model": self.valves.vision_model,
            "max_tokens": self.valves.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": query or "Describe this image in detail."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
        try:
            r = requests.post(
                self.valves.vision_endpoint, json=payload, timeout=self.valves.timeout
            )
            r.raise_for_status()
            answer = r.json()["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            await emit("Vision node offline", True)
            return (
                "The vision node is unreachable right now, so I couldn't look at the image "
                f"(its host may be off). Technical detail: {e}"
            )

        await emit("Image analyzed", True)
        return answer
