"""
title: Kitchen
author: vera
description: Vera's kitchen awareness — Grocy stock (low / expiring / expired) + Mealie recipes and today's meal plan, via vera-api. Call when the user asks what to cook, what's low or expiring, or about meals.
version: 0.1.0
"""
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(
            default="http://localhost:8089",
            description="Base URL of vera-api (hosts the /kitchen/state endpoint).",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def kitchen_status(self, __event_emitter__=None) -> str:
        """
        Get the current kitchen state: staples below minimum stock, items expiring soon or
        already expired, the shopping list, today's meal plan, and the list of available
        recipes. Use this whenever the user asks what to cook, what to buy, what's
        low or expiring, or anything about meals/food on hand.
        """
        async def emit(desc, done=False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": desc, "done": done}})

        await emit("Checking the kitchen...")
        try:
            s = requests.get(f"{self.valves.vera_api_url}/kitchen/state", timeout=20).json()
        except Exception as e:
            await emit("Kitchen service unreachable", True)
            return f"Could not reach the kitchen service: {e}"

        lines = []
        if s.get("below_min"):
            lines.append("Below minimum (needs buying): " + ", ".join(
                f"{x.get('name')} (need {x.get('amount_missing')})" for x in s["below_min"]))
        if s.get("due_soon"):
            lines.append("Expiring soon: " + ", ".join(
                f"{x.get('name')} (by {x.get('best_before')})" for x in s["due_soon"]))
        if s.get("expired"):
            lines.append("Expired: " + ", ".join(x.get("name") for x in s["expired"]))
        if s.get("shopping_list"):
            lines.append("Shopping list: " + ", ".join(x.get("name") for x in s["shopping_list"]))
        if s.get("meal_plan_today"):
            lines.append("Today's meal plan: " + ", ".join(
                (x.get("title") or "?") for x in s["meal_plan_today"]))
        if s.get("recipes"):
            lines.append("Recipes available: " + ", ".join(s["recipes"]))

        await emit("Kitchen checked", True)
        return "\n".join(lines) if lines else "Kitchen looks clear: nothing low or expiring, no meal planned today."
