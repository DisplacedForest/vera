import aiohttp

_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def probe(iid: str, v: dict) -> dict:
    """One round-trip against the integration's API; ok/detail, never raises."""
    url = v.get("url", "").rstrip("/")
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            if iid == "coder":
                async with s.get(f"{url}/models") as r:
                    body = await r.json(content_type=None)
                    ids = [m.get("id") for m in ((body or {}).get("data") or []) if isinstance(m, dict)]
                    if r.status != 200:
                        return {"ok": False, "detail": f"HTTP {r.status}"}
                    want = (v.get("model") or "").strip()
                    if want and ids and want not in ids:
                        return {"ok": True, "detail": f"endpoint up, but '{want}' is not in its model list"}
                    return {"ok": True,
                            "detail": f"endpoint up ({len(ids)} model{'s' if len(ids) != 1 else ''})" if ids
                            else "endpoint up"}
            if iid == "image_gen":
                # Cheap reachability only — a real Images call costs a full generation. The
                # vera reference service answers /health; for a generic OpenAI endpoint any
                # HTTP answer counts (404 there just means "not the vera service").
                async with s.get(f"{url}/health") as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        ok = bool((body or {}).get("ok"))
                        return {"ok": ok, "detail": "image service up" if ok
                                else "endpoint answered /health without ok"}
                    return {"ok": True, "detail": f"endpoint reachable (HTTP {r.status} on /health)"}
            if iid == "home_assistant":
                async with s.get(f"{url}/api/", headers={"Authorization": f"Bearer {v.get('token', '')}"}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and isinstance(body, dict) and "message" in body
                    return {"ok": ok, "detail": body.get("message", f"HTTP {r.status}") if isinstance(body, dict) else f"HTTP {r.status}"}
            if iid == "grocy":
                async with s.get(f"{url}/api/system/info", headers={"GROCY-API-KEY": v.get("api_key", "")}) as r:
                    body = await r.json(content_type=None)
                    ver = (body or {}).get("grocy_version", {})
                    ok = r.status == 200 and bool(ver)
                    return {"ok": ok, "detail": f"Grocy {ver.get('Version', '?')}" if ok else f"HTTP {r.status}"}
            if iid == "mealie":
                async with s.get(f"{url}/api/app/about", headers={"Authorization": f"Bearer {v.get('api_key', '')}"}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and isinstance(body, dict) and "version" in body
                    return {"ok": ok, "detail": f"Mealie {body.get('version', '?')}" if ok else f"HTTP {r.status}"}
            if iid == "overseerr":
                async with s.get(f"{url}/api/v1/status", headers={"X-Api-Key": v.get("api_key", "")}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and isinstance(body, dict) and "version" in body
                    return {"ok": ok, "detail": f"Overseerr {body.get('version', '?')}" if ok else f"HTTP {r.status}"}
            if iid == "unraid":
                hdr = {"x-api-key": v.get("api_key", ""), "Content-Type": "application/json"}
                async with s.post(url, headers=hdr, json={"query": "{ info { os { platform } } }"}) as r:
                    body = await r.json(content_type=None)
                    plat = ((((body or {}).get("data") or {}).get("info") or {}).get("os") or {}).get("platform")
                    return {"ok": bool(plat), "detail": f"Unraid ({plat})" if plat else f"HTTP {r.status}"}
            if iid == "searxng":
                async with s.get(url, params={"q": "connection test", "format": "json"}) as r:
                    ok = r.status == 200
                    return {"ok": ok, "detail": "search responding" if ok else f"HTTP {r.status}"}
            if iid == "embeddings":
                payload = {"model": v.get("model", ""), "input": "vera connection probe"}
                async with s.post(f"{url}/embeddings", json=payload) as r:
                    body = await r.json(content_type=None)
                    vec = (((body or {}).get("data") or [{}])[0] or {}).get("embedding")
                    ok = r.status == 200 and isinstance(vec, list) and len(vec) > 0
                    return {"ok": ok, "detail": f"embedding dim {len(vec)}" if ok else f"HTTP {r.status}"}
            if iid == "apple_reminders":
                async with s.get(f"{url}/health") as r:
                    body = await r.json(content_type=None)
                    granted = bool((body or {}).get("reminders_access"))
                    ok = r.status == 200 and granted
                    detail = ("bridge up, Reminders access granted" if ok else
                              "bridge up, Reminders access NOT granted" if r.status == 200
                              else f"HTTP {r.status}")
                    return {"ok": ok, "detail": detail}
            if iid == "reddit":
                async with s.post("https://www.reddit.com/api/v1/access_token",
                                  auth=aiohttp.BasicAuth(v.get("client_id", ""), v.get("client_secret", "")),
                                  data={"grant_type": "client_credentials"},
                                  headers={"User-Agent": v.get("user_agent") or "vera-scout/1.0"}) as r:
                    body = await r.json(content_type=None)
                    ok = r.status == 200 and bool((body or {}).get("access_token"))
                    return {"ok": ok, "detail": "app-only token acquired" if ok else f"HTTP {r.status}"}
    except Exception as e:  # noqa: BLE001 — a probe reports, never raises
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    return {"ok": False, "detail": "no probe defined"}
