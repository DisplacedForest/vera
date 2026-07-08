"""
title: Reminders
author: vera
description: Read and write Apple Reminders lists (shared lists included) via vera-api. Call for anything about a reminders or shopping list: reading it, adding items, checking items off.
version: 0.1.0
"""
import requests
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        vera_api_url: str = Field(
            default="http://localhost:8089",
            description="Base URL of vera-api (hosts the /reminders endpoints).",
        )
        default_list: str = Field(
            default="",
            description="Reminders list used when the user does not name one (e.g. the household shopping list).",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _list(self, list_name: str) -> str:
        return list_name.strip() or self.valves.default_list

    def get_reminder_lists(self) -> str:
        """List the available Reminders lists by name."""
        try:
            r = requests.get(f"{self.valves.vera_api_url}/reminders/lists", timeout=20)
            data = r.json()
        except Exception as e:
            return f"Could not reach the reminders service: {e}"
        if r.status_code != 200:
            return f"Reminders unavailable: {data.get('detail', r.status_code)}"
        names = [x["name"] for x in data.get("lists", [])]
        return "Reminders lists: " + ", ".join(names) if names else "No reminders lists found."

    def get_reminders(self, list_name: str = "") -> str:
        """Read the open items on a Reminders list. Use for questions like what is on the shopping list. list_name is optional when a default list is configured."""
        target = self._list(list_name)
        params = {"list": target} if target else {}
        try:
            r = requests.get(f"{self.valves.vera_api_url}/reminders", params=params, timeout=20)
            data = r.json()
        except Exception as e:
            return f"Could not reach the reminders service: {e}"
        if r.status_code != 200:
            return f"Reminders unavailable: {data.get('detail', r.status_code)}"
        items = data.get("reminders", [])
        if not items:
            return f"{target or 'Reminders'} is empty."
        lines = [f"- {i['title']}" + (f" (due {i['due']})" if i.get("due") else "") for i in items]
        return f"Open items on {target or 'your reminders'}:\n" + "\n".join(lines)

    def add_reminder(self, title: str, list_name: str = "", due: str = "") -> str:
        """Add an item to a Reminders list. due is an optional ISO date or datetime. list_name is optional when a default list is configured."""
        target = self._list(list_name)
        if not target:
            return "No list named and no default list configured. Ask which list to use."
        body = {"list": target, "title": title}
        if due:
            body["due"] = due
        try:
            r = requests.post(f"{self.valves.vera_api_url}/reminders", json=body, timeout=20)
            data = r.json()
        except Exception as e:
            return f"Could not reach the reminders service: {e}"
        if r.status_code != 200:
            return f"Could not add the reminder: {data.get('detail', r.status_code)}"
        stored = (data.get("reminder") or {}).get("title", title)
        return f"Added '{stored}' to {target}."

    def complete_reminder(self, title: str, list_name: str = "") -> str:
        """Mark an item on a Reminders list as done, matched by its title. list_name is optional when a default list is configured."""
        target = self._list(list_name)
        params = {"list": target} if target else {}
        try:
            r = requests.get(f"{self.valves.vera_api_url}/reminders", params=params, timeout=20)
            data = r.json()
        except Exception as e:
            return f"Could not reach the reminders service: {e}"
        if r.status_code != 200:
            return f"Reminders unavailable: {data.get('detail', r.status_code)}"
        want = title.strip().lower()
        matches = [i for i in data.get("reminders", []) if i["title"].strip().lower() == want]
        if not matches:
            matches = [i for i in data.get("reminders", []) if want in i["title"].strip().lower()]
        if not matches:
            return f"No open item matching '{title}' on {target or 'your reminders'}."
        if len(matches) > 1:
            return "Multiple items match: " + ", ".join(i["title"] for i in matches) + ". Ask which one."
        rid = matches[0]["id"]
        try:
            r = requests.patch(f"{self.valves.vera_api_url}/reminders/{rid}",
                               json={"completed": True}, timeout=20)
            data = r.json()
        except Exception as e:
            return f"Could not reach the reminders service: {e}"
        if r.status_code != 200:
            return f"Could not complete the reminder: {data.get('detail', r.status_code)}"
        return f"Checked off '{matches[0]['title']}' on {target or 'your reminders'}."
