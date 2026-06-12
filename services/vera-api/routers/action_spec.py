"""Action specs — pure validators, previews, and the HA allowlist. No deps, unit-testable.

The async executors (which call HA / Grocy / knowledge_store) live in actions.py and are attached to
these specs to form the full REGISTRY. Keeping the pure half here means the allowlist and validation
— the safety-critical logic — can be tested without any network or package context.
"""
import os


def _env_set(name: str, default: str) -> set[str]:
    """A comma-separated set-valued env var, falling back to the default when unset."""
    raw = os.environ.get(name, "").strip() or default
    return {p.strip() for p in raw.split(",") if p.strip()}


# ha.service is the only physical-actuation verb; restrict it hard. What the household allows is
# config: HA_ALLOWED_SERVICES grants exact domain.service calls, HA_ALLOWED_DOMAINS grants any
# service on a domain (turn_on/off/toggle). Defaults cover reversible comfort/scene/script/media
# calls plus update.install — the apply path for the pending-updates Pulse card (HA Core, add-ons,
# HACS, any integration-exposed firmware/agent update).
HA_ALLOWED_SERVICES = _env_set(
    "HA_ALLOWED_SERVICES", "climate.set_temperature,scene.turn_on,script.turn_on,update.install")
HA_ALLOWED_DOMAINS = _env_set("HA_ALLOWED_DOMAINS", "light,switch,media_player")


def ha_allowed(domain, service):
    return f"{domain}.{service}" in HA_ALLOWED_SERVICES or domain in HA_ALLOWED_DOMAINS


def _v_ha(args):
    d, s = args.get("domain"), args.get("service")
    if not d or not s:
        return "ha.service needs domain and service"
    if not ha_allowed(d, s):
        return f"{d}.{s} is not allowlisted"
    return None


def _p_ha(args):
    data = args.get("data") or {}
    tgt = data.get("entity_id") or "?"
    return f"Call {args.get('domain')}.{args.get('service')} on {tgt} with {data}"


def _v_kset(args):
    if not args.get("type") or not args.get("name"):
        return "knowledge.set needs type and name"
    return None


def _p_kset(args):
    return f"Record {args.get('type')} '{args.get('name')}': {args.get('attrs') or {}}"


def _v_kdel(args):
    if not args.get("entity_id") and not (args.get("type") and args.get("name")):
        return "knowledge.delete needs entity_id or type+name"
    return None


def _p_kdel(args):
    target = args.get("entity_id") or f"{args.get('type')}:{args.get('name')}"
    return f"Delete {target}"


def _v_kreverify(args):
    if not args.get("entity_id"):
        return "knowledge.reverify needs entity_id"
    return None


def _p_kreverify(args):
    return f"Re-verify {args.get('entity_id')} (stamp last_verified = now)"


def _v_kpromote(args):
    if not args.get("type") or not args.get("schema"):
        return "knowledge.promote needs type and schema"
    return None


def _p_kpromote(args):
    req = (args.get("schema") or {}).get("required") or []
    return f"Codify the '{args.get('type')}' type's schema (required: {', '.join(req) or 'none'})"


def _v_grocy(args):
    if not args.get("product_id"):
        return "kitchen.grocy_adjust needs product_id"
    if args.get("op") not in ("add", "consume"):
        return "op must be add or consume"
    if not args.get("amount"):
        return "kitchen.grocy_adjust needs amount"
    return None


def _p_grocy(args):
    return f"Grocy {args.get('op')} {args.get('amount')} of product {args.get('product_id')}"


def _v_health(args):
    return None


def _p_health(args):
    return "Run a read-only server/home health check"


def _v_mealie_import(args):
    if not args.get("url"):
        return "kitchen.mealie_import needs url"
    return None


def _p_mealie_import(args):
    return f"Import the recipe at {args.get('url')} into Mealie (structured)"


def _v_docker_update(args):
    # Either is enough to resolve the container via the host's container-management API; name preferred.
    if not args.get("name") and not args.get("image"):
        return "docker.update needs name or image"
    return None


def _p_docker_update(args):
    who = args.get("name") or args.get("image")
    detail = f" ({args['image']})" if args.get("name") and args.get("image") else ""
    return f"Update container {who}{detail}. Pull the new image and recreate it"


def _v_overseerr(args):
    if args.get("media_type") not in ("movie", "tv"):
        return "overseerr_request needs media_type movie|tv"
    if not args.get("media_id"):
        return "overseerr_request needs media_id"
    return None


def _p_overseerr(args):
    label = args.get("title") or f"tmdb {args.get('media_id')}"
    return f"Request {label} ({args.get('media_type')}) to the media library"


# verb -> pure spec. Executors are attached in actions.py.
# Each verb's `summary` and `args` shape ship to clients via GET /actions/registry — the
# discovery surface tools use instead of hardcoding this catalog. Declared beside the
# validators so the advertised shape and the enforced shape can't drift apart.
# `autonomous` is the trust-graduated free lane: True lets the verb execute via
# POST /actions/auto with NO confirm gate. It is an explicit per-verb enrollment — a
# deliberate one-line act, never derived from risk/reversible — and every entry carries
# it so flipping a verb free is always a visible diff.
SPEC = {
    "ha.service": {"validate": _v_ha, "preview": _p_ha, "risk": "medium", "reversible": True,
                   "autonomous": False,
                   "summary": "call a Home Assistant service (the server enforces a configurable allowlist)",
                   "args": '{"domain": "...", "service": "...", "data": {"entity_id": "...", ...}}'},
    "knowledge.set": {"validate": _v_kset, "preview": _p_kset, "risk": "low", "reversible": True,
                      "autonomous": False,
                      "summary": "record or update a durable home fact",
                      "args": '{"type": "...", "name": "...", "attrs": {...}}'},
    "knowledge.delete": {"validate": _v_kdel, "preview": _p_kdel, "risk": "medium", "reversible": False,
                         "autonomous": False,
                         "summary": "remove a durable home fact",
                         "args": '{"entity_id": "..."} or {"type": "...", "name": "..."}'},
    "knowledge.reverify": {"validate": _v_kreverify, "preview": _p_kreverify, "risk": "none", "reversible": True,
                           "autonomous": False,
                           "summary": "re-stamp a fact's last_verified to now",
                           "args": '{"entity_id": "..."}'},
    "knowledge.promote": {"validate": _v_kpromote, "preview": _p_kpromote, "risk": "low", "reversible": True,
                          "autonomous": False,
                          "summary": "codify a knowledge type's schema",
                          "args": '{"type": "...", "schema": {...}}'},
    "kitchen.grocy_adjust": {"validate": _v_grocy, "preview": _p_grocy, "risk": "low", "reversible": True,
                             "autonomous": False,
                             "summary": "adjust kitchen stock (op is add or consume)",
                             "args": '{"product_id": N, "op": "add|consume", "amount": N}'},
    "kitchen.mealie_import": {"validate": _v_mealie_import, "preview": _p_mealie_import, "risk": "low", "reversible": True,
                              "autonomous": True,  # the sole free-lane verb: cheap, undoable curation
                              "summary": "import a recipe by URL",
                              "args": '{"url": "..."}'},
    "health.check": {"validate": _v_health, "preview": _p_health, "risk": "none", "reversible": True,
                     "autonomous": False,
                     "summary": "run a service health check now",
                     "args": "{}"},
    "overseerr_request": {"validate": _v_overseerr, "preview": _p_overseerr, "risk": "low", "reversible": True,
                          "autonomous": False,
                          "summary": "request a title for the media library",
                          "args": '{"media_type": "movie|tv", "media_id": N, "title": "..."}'},
    "docker.update": {"validate": _v_docker_update, "preview": _p_docker_update, "risk": "high", "reversible": False,
                      "autonomous": False,
                      "summary": "update one container image (bounces the container)",
                      "args": '{"name": "...", "image": "..."}'},
}


def is_autonomous(verb: str) -> bool:
    """True only for verbs explicitly enrolled in the free (no-confirm) lane."""
    return bool((SPEC.get(verb) or {}).get("autonomous"))


def check_autonomy_invariant(spec: dict) -> None:
    """A verb may be autonomous only if it is genuinely cheap to be wrong about:
    risk in {none, low} AND reversible. Raises so a dangerous verb can never be
    flagged free, even by mistake."""
    for verb, s in spec.items():
        if s.get("autonomous") and not (s["risk"] in ("none", "low") and s["reversible"]):
            raise AssertionError(
                f"{verb} cannot be autonomous: requires risk in {{none,low}} and reversible "
                f"(got risk={s['risk']!r}, reversible={s['reversible']!r})")


check_autonomy_invariant(SPEC)
