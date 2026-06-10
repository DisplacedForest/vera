"""Kitchen intelligence — Grocy stock + Mealie recipes/meal-plan as a grounding source.

Two surfaces:
  GET  /kitchen/state  -> consolidated snapshot (below-min, expiring, expired, shopping
                          list, today's meal plan, recipe names). The OWUI `kitchen`
                          tool calls this; Vera reasons about cook-from-stock herself.
  POST /kitchen/check  -> zero-floor Pulse card: only writes a card when there's
                          something worth saying (low staples / expiring / a planned meal).

Grocy and Mealie connect through the integration registry (url + api key each); either
alone degrades to its own half — Grocy = stock/expiry/shopping, Mealie = recipes/plan.
The write path (recipe discovery -> proper Mealie import) creates fully structured
recipes: NLP-parsed ingredients + reuse-first cuisine/tag classification.
"""

import json
import os

import aiohttp
from fastapi import APIRouter
from pydantic import BaseModel

from .pulse import DEFAULT_FOLDER, _inject, _vera
from .persona import owner, voiced

router = APIRouter()

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_PARSE_TIMEOUT = aiohttp.ClientTimeout(total=45)
# Grocy supports multiple shopping lists; writes target this one (Grocy's default list is id 1).
GROCY_SHOPPING_LIST_ID = int(os.environ.get("GROCY_SHOPPING_LIST_ID", "1"))


# Registry lookups at call time so runtime enable/disable applies without a restart.

def _grocy_url() -> str:
    from . import integrations
    return (integrations.integration("grocy") or {}).get("url", "")


def _grocy_key() -> str:
    from . import integrations
    return (integrations.integration("grocy") or {}).get("api_key", "")


def _mealie_url() -> str:
    from . import integrations
    return (integrations.integration("mealie") or {}).get("url", "")


def _mealie_key() -> str:
    from . import integrations
    return (integrations.integration("mealie") or {}).get("api_key", "")


def _mealie_hdr() -> dict:
    return {"Authorization": f"Bearer {_mealie_key()}"}


async def _grocy(session, path):
    async with session.get(f"{_grocy_url()}{path}", headers={"GROCY-API-KEY": _grocy_key()}, timeout=_TIMEOUT) as r:
        return await r.json()


async def _mealie(session, path):
    async with session.get(f"{_mealie_url()}{path}", headers={"Authorization": f"Bearer {_mealie_key()}"}, timeout=_TIMEOUT) as r:
        return await r.json()


def _json_obj(raw: str) -> dict:
    """Pull the first JSON object out of an LLM reply (tolerates ```json fences / prose)."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw[:4].lower() == "json":
            raw = raw[4:]
    a, b = raw.find("{"), raw.rfind("}")
    if a != -1 and b != -1:
        raw = raw[a:b + 1]
    return json.loads(raw)


async def _ensure(s, kind: str, name: str, cache: dict) -> dict | None:
    """Resolve a food/unit name to a persisted object, creating it if missing (what the UI Parse
    button does for unknown foods). kind in {foods, units}. Cache is name-keyed to avoid dupes."""
    key = name.strip().lower()
    if key in cache:
        return cache[key]
    async with s.post(f"{_mealie_url()}/api/{kind}", headers=_mealie_hdr(), json={"name": name}, timeout=_TIMEOUT) as r:
        if r.status < 300:
            obj = await r.json()
            cache[key] = obj
            return obj
    async with s.get(f"{_mealie_url()}/api/{kind}?perPage=2000", headers=_mealie_hdr(), timeout=_TIMEOUT) as r:
        for it in (await r.json()).get("items", []):
            if it["name"].strip().lower() == key:
                cache[key] = it
                return it
    return None


async def _parse_ingredients(s, recipe) -> list | None:
    """Run Mealie's NLP parser over the scraped ingredient lines (what the UI Parse button does)
    and return a structured recipeIngredient list with linked foods/units. The standalone parser
    only links foods/units that already exist; for new ones it returns id-less objects that the
    recipe PUT rejects, so we create-or-reuse each to get an id. None if nothing to do."""
    orig = recipe.get("recipeIngredient") or []
    lines = [i.get("display") or i.get("note") or i.get("originalText") for i in orig]
    lines = [t for t in lines if t]
    if not lines:
        return None
    async with s.post(f"{_mealie_url()}/api/parser/ingredients", headers=_mealie_hdr(),
                      json={"parser": "nlp", "ingredients": lines}, timeout=_PARSE_TIMEOUT) as r:
        if r.status >= 300:
            raise RuntimeError(f"parser {r.status}: {(await r.text())[:120]}")
        parsed = await r.json()

    food_cache: dict = {}
    unit_cache: dict = {}
    async with s.get(f"{_mealie_url()}/api/foods?perPage=2000", headers=_mealie_hdr(), timeout=_TIMEOUT) as r:
        for it in (await r.json()).get("items", []):
            food_cache[it["name"].strip().lower()] = it
    async with s.get(f"{_mealie_url()}/api/units?perPage=500", headers=_mealie_hdr(), timeout=_TIMEOUT) as r:
        for it in (await r.json()).get("items", []):
            unit_cache[it["name"].strip().lower()] = it

    out = []
    for idx, p in enumerate(parsed):
        ing = p.get("ingredient") or {}
        f, u = ing.get("food"), ing.get("unit")
        if isinstance(f, dict) and f.get("name") and not f.get("id"):
            ing["food"] = await _ensure(s, "foods", f["name"], food_cache)  # may be None -> unlinked
        if isinstance(u, dict) and u.get("name") and not u.get("id"):
            ing["unit"] = await _ensure(s, "units", u["name"], unit_cache)
        if idx < len(orig) and orig[idx].get("referenceId"):
            ing["referenceId"] = orig[idx]["referenceId"]  # keep instruction links intact
        ing["disableAmount"] = False
        out.append(ing)
    return out


async def _create_org(s, kind: str, name: str) -> dict | None:
    """Create a category/tag, or fetch it if it already exists. kind in {categories, tags}."""
    async with s.post(f"{_mealie_url()}/api/organizers/{kind}", headers=_mealie_hdr(),
                      json={"name": name}, timeout=_TIMEOUT) as r:
        if r.status < 300:
            return await r.json()
    async with s.get(f"{_mealie_url()}/api/organizers/{kind}?perPage=400", headers=_mealie_hdr(), timeout=_TIMEOUT) as r:
        for it in (await r.json()).get("items", []):
            if it["name"].lower() == name.lower():
                return it
    return None


def _orthogonal_categories() -> dict[str, str]:
    """Mealie categories the household treats as orthogonal axes — categories that apply
    alongside a cuisine (a preservation method, a dietary track, …) rather than competing with
    one. Config: MEALIE_ORTHOGONAL_CATEGORIES, ';'-separated entries, each 'Name' or
    'Name=a hint telling the classifier when the axis applies'. Default empty: every existing
    category is treated as a cuisine."""
    out: dict[str, str] = {}
    for entry in os.environ.get("MEALIE_ORTHOGONAL_CATEGORIES", "").split(";"):
        name, _, hint = entry.partition("=")
        if name.strip():
            out[name.strip()] = hint.strip()
    return out


async def _classify(s, recipe) -> tuple[list, list]:
    """Vera picks ONE cuisine category (+ any configured orthogonal axes that apply) + a few
    tags, reuse-first from the household's existing Mealie taxonomy; new organizers only when
    nothing fits. Returns (category_objs, tag_objs)."""
    async with s.get(f"{_mealie_url()}/api/organizers/categories?perPage=300", headers=_mealie_hdr(), timeout=_TIMEOUT) as r:
        cats = {c["name"]: c for c in (await r.json()).get("items", [])}
    async with s.get(f"{_mealie_url()}/api/organizers/tags?perPage=400", headers=_mealie_hdr(), timeout=_TIMEOUT) as r:
        tags = {t["name"]: t for t in (await r.json()).get("items", [])}

    foods = [(i.get("food") or {}).get("name") for i in (recipe.get("recipeIngredient") or [])]
    foods = [f for f in foods if f]
    ortho = _orthogonal_categories()
    cuisines = sorted(c for c in cats if c not in ortho)  # orthogonal axes are not cuisines
    axis_lines = "".join(
        f"- {n}: true ONLY if the recipe genuinely belongs to this axis"
        + (f" ({h})" if h else "") + "; else false.\n"
        for n, h in ortho.items())
    axes_json = ('"axes": {' + ", ".join(f'"{n}": false' for n in ortho) + "}, ") if ortho else ""
    sys = (
        "You classify a recipe for a personal Mealie library that uses category axes plus tags:\n"
        "- cuisine: the single best cuisine from the existing list, or null if the recipe has no "
        "clear cuisine. Only propose new_cuisine if none truly fit.\n"
        + axis_lines +
        "- tags: 2-5 tags chosen ONLY from the existing list; reuse aggressively, never invent a "
        "near-duplicate of an existing tag. Only add new_tags if genuinely nothing fits.\n"
        'Reply with STRICT JSON and nothing else: {"cuisine": "<name|null>", ' + axes_json +
        '"tags": ["<name>"], "new_cuisine": null, "new_tags": []}'
    )
    usr = (
        f"Recipe: {recipe.get('name')}\n"
        f"Ingredients: {', '.join(foods)}\n"
        f"Source: {recipe.get('orgURL') or ''}\n\n"
        f"Existing cuisines: {cuisines}\n"
        f"Existing tags: {sorted(tags)}"
    )
    data = _json_obj(await _vera([{"role": "system", "content": sys},
                                  {"role": "user", "content": usr}], temperature=0.2))

    chosen_cats: list = []
    cu = data.get("cuisine")
    if cu and cu in cats:
        chosen_cats.append(cats[cu])
    elif data.get("new_cuisine"):
        obj = await _create_org(s, "categories", data["new_cuisine"])
        if obj:
            chosen_cats.append(obj)
    for axis, applies in (data.get("axes") or {}).items():  # additive, independent of cuisine
        if not applies or axis not in ortho:
            continue
        obj = cats.get(axis) or await _create_org(s, "categories", axis)
        if obj:
            chosen_cats.append(obj)

    chosen_tags: list = []
    for t in data.get("tags") or []:
        if t in tags:
            chosen_tags.append(tags[t])
    for t in data.get("new_tags") or []:
        obj = await _create_org(s, "tags", t)
        if obj:
            chosen_tags.append(obj)
    return chosen_cats, chosen_tags


async def import_recipe(url: str) -> dict:
    """The recipe write path: scrape a recipe URL into Mealie, then enrich it the way a human
    would — NLP-parse the ingredients into linked foods/units and classify it (one cuisine category
    + reuse-first tags). Called only by the gated `kitchen.mealie_import` verb (confirm-gated).
    Enrichment is best-effort: the recipe is always created; failures surface in `warnings`."""
    if not (_mealie_url() and _mealie_key()):
        return {"ok": False, "error": "Mealie not configured"}
    warnings: list = []
    try:
        async with aiohttp.ClientSession() as s:
            # 1. Scrape. includeTags=False: we classify into our own taxonomy, never the source's.
            async with s.post(f"{_mealie_url()}/api/recipes/create/url", headers=_mealie_hdr(),
                              json={"url": url, "includeTags": False},
                              timeout=aiohttp.ClientTimeout(total=90)) as r:
                txt = await r.text()
                if r.status >= 300:
                    return {"ok": False, "status": r.status, "error": txt[:200]}
            slug = txt.strip().strip('"')  # Mealie returns the new recipe's slug as a bare JSON string
            if not slug:
                return {"ok": False, "error": "no slug returned"}

            async with s.get(f"{_mealie_url()}/api/recipes/{slug}", headers=_mealie_hdr(), timeout=_TIMEOUT) as r:
                recipe = await r.json()

            # 2. Parse ingredients into structured, linked foods/units (best-effort).
            try:
                structured = await _parse_ingredients(s, recipe)
                if structured:
                    recipe["recipeIngredient"] = structured
            except Exception as e:
                warnings.append(f"ingredient parse failed: {e}")

            # 3. Classify into the cuisine/tag taxonomy (best-effort).
            try:
                cats, recipe_tags = await _classify(s, recipe)
                if cats:
                    recipe["recipeCategory"] = cats
                if recipe_tags:
                    recipe["tags"] = recipe_tags
            except Exception as e:
                warnings.append(f"classify failed: {e}")

            # 4. Write the enriched recipe back in one PUT.
            async with s.put(f"{_mealie_url()}/api/recipes/{slug}", headers=_mealie_hdr(),
                             json=recipe, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status >= 300:
                    warnings.append(f"update failed {r.status}: {(await r.text())[:120]}")

        return {
            "ok": True, "slug": slug, "url": f"{_mealie_url()}/g/home/r/{slug}",
            "category": [c["name"] for c in recipe.get("recipeCategory") or []],
            "tags": [t["name"] for t in recipe.get("tags") or []],
            "warnings": warnings,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


class ImportRecipe(BaseModel):
    url: str


@router.post("/kitchen/import_recipe", tags=["kitchen"])
async def import_recipe_ep(req: ImportRecipe):
    return await import_recipe(req.url)


def _row(item):
    """Normalize a Grocy stock row (due/overdue/expired) to {name, best_before, amount}."""
    prod = item.get("product") or {}
    return {
        "name": item.get("name") or prod.get("name") or f"product {item.get('product_id', '?')}",
        "best_before": item.get("best_before_date"),
        "amount": item.get("amount"),
    }


async def gather_state() -> dict:
    """Pull and normalize the kitchen snapshot from Grocy + Mealie."""
    state = {
        "below_min": [], "due_soon": [], "expired": [],
        "shopping_list": [], "meal_plan_today": [], "recipes": [],
        "sources": {"grocy": bool(_grocy_url() and _grocy_key()), "mealie": bool(_mealie_url() and _mealie_key())},
        "errors": [],
    }
    async with aiohttp.ClientSession() as s:
        # Grocy: volatile stock (below-min / due / overdue+expired)
        try:
            v = await _grocy(s, "/api/stock/volatile")
            state["below_min"] = [
                {"name": m.get("name"), "amount_missing": m.get("amount_missing")}
                for m in v.get("missing_products", []) or []
            ]
            state["due_soon"] = [_row(x) for x in (v.get("due_products") or [])]
            state["expired"] = [_row(x) for x in (v.get("overdue_products") or [])] + \
                               [_row(x) for x in (v.get("expired_products") or [])]
        except Exception as e:
            state["errors"].append(f"grocy volatile: {e}")
        # Grocy: shopping list (resolve product names if any items)
        try:
            items = await _grocy(s, "/api/objects/shopping_list")
            if items:
                prods = await _grocy(s, "/api/objects/products")
                names = {str(p["id"]): p["name"] for p in prods}
                state["shopping_list"] = [
                    {"name": names.get(str(it.get("product_id")), it.get("note") or "item"),
                     "amount": it.get("amount")}
                    for it in items
                ]
        except Exception as e:
            state["errors"].append(f"grocy shopping_list: {e}")
        # Mealie: today's plan + recipe names
        try:
            plan = await _mealie(s, "/api/households/mealplans/today")
            state["meal_plan_today"] = [
                {"type": p.get("entryType"), "title": (p.get("recipe") or {}).get("name") or p.get("title")}
                for p in (plan or [])
            ]
        except Exception as e:
            state["errors"].append(f"mealie mealplan: {e}")
        try:
            rec = await _mealie(s, "/api/recipes?perPage=100&orderBy=name&orderDirection=asc")
            state["recipes"] = [i.get("name") for i in (rec.get("items") or [])]
        except Exception as e:
            state["errors"].append(f"mealie recipes: {e}")
    return state


@router.get("/kitchen/state", tags=["kitchen"])
async def kitchen_state():
    return await gather_state()


class KitchenCheck(BaseModel):
    pulse_folder_id: str | None = None


@router.post("/kitchen/check", tags=["kitchen"])
async def kitchen_check(req: KitchenCheck):
    """Zero-floor Pulse card: only inject when there's something worth surfacing."""
    state = await gather_state()
    out = {"ok": True, "injected": False, "state": state}
    notable = state["below_min"] or state["due_soon"] or state["expired"] or state["meal_plan_today"]
    if not notable:
        return out  # quiet kitchen, no card

    sys = (
        f"Write a short kitchen card for {owner()}. "
        "If items are expiring, lead with that and suggest using them (you know their recipes). "
        "If staples are below min, note a short shopping-list nudge. If a meal is planned today, mention it. "
        "Reason about what they could cook from what's on hand + their recipe list. 2-4 sentences, "
        "GitHub-flavored markdown, no preamble. If nothing is genuinely useful, reply exactly: SKIP."
    )
    usr = (
        f"Below min: {state['below_min']}\n"
        f"Expiring soon: {state['due_soon']}\n"
        f"Expired: {state['expired']}\n"
        f"Today's meal plan: {state['meal_plan_today']}\n"
        f"Recipes available: {state['recipes']}"
    )
    body = (await _vera([{"role": "system", "content": voiced(sys)}, {"role": "user", "content": usr}], temperature=0.5)).strip()
    if body.upper().startswith("SKIP") or not body:
        return out
    title = "Kitchen · " + (
        f"{len(state['due_soon'])} expiring" if state["due_soon"]
        else f"{len(state['below_min'])} staples low" if state["below_min"]
        else "today's plan"
    )
    folder = req.pulse_folder_id or DEFAULT_FOLDER
    await _inject(title, body, folder)
    out["injected"] = True
    out["title"] = title
    return out


# ──────────────────────────────────────────────────────────────────────────
# Kitchen-dashboard management surface: compact reads + write-through to Grocy
# so the HA dashboard is the single surface — no link-out to Grocy/Mealie.
# HA calls these over the LAN; the Grocy/Mealie creds stay here.
# ──────────────────────────────────────────────────────────────────────────

async def _grocy_post(session, path, payload):
    async with session.post(f"{_grocy_url()}{path}", headers={"GROCY-API-KEY": _grocy_key(), "Content-Type": "application/json"},
                            json=payload, timeout=_TIMEOUT) as r:
        return r.status, await r.text()


async def _grocy_delete(session, path):
    async with session.delete(f"{_grocy_url()}{path}", headers={"GROCY-API-KEY": _grocy_key()}, timeout=_TIMEOUT) as r:
        return r.status, await r.text()


@router.get("/kitchen/inventory", tags=["kitchen"])
async def kitchen_inventory():
    """Full editable stock list for the kitchen dashboard's pantry manager (compact, name-sorted, low first)."""
    async with aiohttp.ClientSession() as s:
        try:
            stock = await _grocy(s, "/api/stock")
        except Exception as e:
            return {"items": [], "count": 0, "source_ok": False, "error": str(e)}
        try:
            units = {u["id"]: u.get("name") for u in await _grocy(s, "/api/objects/quantity_units")}
        except Exception:
            units = {}
    items = []
    for e in stock or []:
        p = e.get("product") or {}
        amt = e.get("amount") or 0
        mn = p.get("min_stock_amount") or 0
        items.append({
            "id": p.get("id") or e.get("product_id"),
            "name": p.get("name"),
            "amount": amt,
            "unit": units.get(p.get("qu_id_stock")),
            "min": mn,
            "opened": e.get("amount_opened") or 0,
            "best_before": e.get("best_before_date"),
            "low": bool(mn) and amt < mn,
            "quick": p.get("quick_consume_amount") or 1,
        })
    items.sort(key=lambda x: (not x["low"], (x["name"] or "").lower()))
    return {"items": items, "count": len(items), "source_ok": bool(_grocy_url() and _grocy_key())}


class ProductAmount(BaseModel):
    product_id: int
    amount: float = 1


@router.post("/kitchen/consume", tags=["kitchen"])
async def kitchen_consume(req: ProductAmount):
    async with aiohttp.ClientSession() as s:
        st, txt = await _grocy_post(s, f"/api/stock/products/{req.product_id}/consume",
                                    {"amount": req.amount, "transaction_type": "consume", "spoiled": False})
    return {"ok": st < 300, "status": st, "detail": None if st < 300 else txt[:200]}


@router.post("/kitchen/open", tags=["kitchen"])
async def kitchen_open(req: ProductAmount):
    async with aiohttp.ClientSession() as s:
        st, txt = await _grocy_post(s, f"/api/stock/products/{req.product_id}/open", {"amount": req.amount})
    return {"ok": st < 300, "status": st, "detail": None if st < 300 else txt[:200]}


class AddProduct(BaseModel):
    product_id: int
    amount: float = 1
    best_before_date: str | None = None


@router.post("/kitchen/add", tags=["kitchen"])
async def kitchen_add(req: AddProduct):
    # Grocy requires a best-before on add; default to "never" so a quick add just works.
    payload = {"amount": req.amount, "transaction_type": "purchase",
               "best_before_date": req.best_before_date or "2999-12-31"}
    async with aiohttp.ClientSession() as s:
        st, txt = await _grocy_post(s, f"/api/stock/products/{req.product_id}/add", payload)
    return {"ok": st < 300, "status": st, "detail": None if st < 300 else txt[:200]}


class ShoppingAdd(BaseModel):
    product_id: int | None = None
    note: str | None = None
    amount: float = 1


@router.post("/kitchen/shopping/add", tags=["kitchen"])
async def kitchen_shopping_add(req: ShoppingAdd):
    async with aiohttp.ClientSession() as s:
        if req.product_id:
            st, txt = await _grocy_post(s, "/api/stock/shoppinglist/add-product",
                                        {"product_id": req.product_id, "list_id": GROCY_SHOPPING_LIST_ID,
                                         "product_amount": req.amount})
        else:
            st, txt = await _grocy_post(s, "/api/objects/shopping_list",
                                        {"note": req.note or "item", "shopping_list_id": GROCY_SHOPPING_LIST_ID,
                                         "amount": req.amount})
    return {"ok": st < 300, "status": st, "detail": None if st < 300 else txt[:200]}


class ShoppingRemove(BaseModel):
    id: int


@router.post("/kitchen/shopping/remove", tags=["kitchen"])
async def kitchen_shopping_remove(req: ShoppingRemove):
    async with aiohttp.ClientSession() as s:
        st, txt = await _grocy_delete(s, f"/api/objects/shopping_list/{req.id}")
    return {"ok": st < 300, "status": st, "detail": None if st < 300 else txt[:200]}


@router.get("/kitchen/recipes", tags=["kitchen"])
async def kitchen_recipes():
    """Recipe index (slug + name) for the kitchen dashboard's recipe browser."""
    async with aiohttp.ClientSession() as s:
        try:
            rec = await _mealie(s, "/api/recipes?perPage=200&orderBy=name&orderDirection=asc")
        except Exception as e:
            return {"items": [], "count": 0, "source_ok": False, "error": str(e)}
    items = [{"slug": i.get("slug"), "name": i.get("name")} for i in (rec.get("items") or []) if i.get("slug")]
    return {"items": items, "count": len(items), "source_ok": bool(_mealie_url() and _mealie_key())}


@router.get("/kitchen/recipe/{slug}", tags=["kitchen"])
async def kitchen_recipe(slug: str):
    """Full recipe detail for native in-dashboard reading (ingredients + steps)."""
    async with aiohttp.ClientSession() as s:
        try:
            r = await _mealie(s, f"/api/recipes/{slug}")
        except Exception as e:
            return {"slug": slug, "error": str(e)}
    ings = [(i.get("display") or i.get("note") or i.get("originalText") or "").strip()
            for i in (r.get("recipeIngredient") or [])]
    steps = [(i.get("text") or "").strip() for i in (r.get("recipeInstructions") or [])]
    rid = r.get("id")
    return {
        "slug": slug,
        "name": r.get("name"),
        "description": r.get("description"),
        "servings": r.get("recipeYield"),
        "total_time": r.get("totalTime"),
        "ingredients": [x for x in ings if x],
        "instructions": [x for x in steps if x],
        "image": f"{_mealie_url()}/api/media/recipes/{rid}/images/min-original.webp" if rid else None,
    }
