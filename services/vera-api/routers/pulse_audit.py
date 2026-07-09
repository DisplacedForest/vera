import json
from datetime import datetime

import aiohttp

from . import pulse_store as store
from .pulse_llm import TZ
from .pulse_synthesis import _numbered_corpus, _split_headline


AUDIT_SYS = (
    "You are a strict fact auditor. Given a briefing and its numbered sources, list the briefing's "
    "key factual assertions — above all CURRENT-STATE claims (who holds a role, who manages, who "
    "employs whom, who plays where today), plus headline figures and named events. For each, give "
    "the number of one source that supports it, or the word UNSUPPORTED when no source does. Judge "
    "ONLY against the sources; what you believe about the world does not count. Return ONLY JSON: "
    '{"claims":[{"claim":"...","source":3},{"claim":"...","source":"UNSUPPORTED"}]}'
)

REVISE_SYS = (
    "Revise the briefing below with surgical precision: the listed claims are NOT supported by its "
    "sources and must be removed or hedged — drop the unsupported attribution, never substitute a "
    "different specific. Change nothing else: keep every other sentence, citation marker, image "
    "token, and block exactly as written. Output starts with the same 'HEADLINE: ' line (rewritten "
    "only if it contains an unsupported claim), then a blank line, then the briefing."
)


async def _auditor(messages):
    """The audit model: the coder endpoint when configured AND reachable — a DIFFERENT model
    than the writer, so the writer's priors can't validate their own fabrication. The coder is
    typically an on-demand server, so unreachable is a normal state, not an error: fall back to
    a main-model self-audit (weaker, still better than none) and name the fallback. Returns
    (reply_text, auditor_name, provenance_stamp) — the stamp is what the card records."""
    from . import pulse
    from . import coder  # lazy: avoids a circular load at import time
    base, model = coder._endpoint()
    if base:
        try:
            # Explicit generation budget: a full claims enumeration overruns the small
            # default cap some servers apply, truncating the verdict JSON mid-object.
            msg = await coder._llm(messages, 0.0, max_tokens=3000)
            return (msg.get("content") or ""), "coder", f"cross-model ({model or 'coder'})"
        except Exception:
            return await pulse._vera(messages, temperature=0.0), "main model (coder unreachable)", "self (fallback)"
    return await pulse._vera(messages, temperature=0.0), "main model (coder unconfigured)", "self (fallback)"


def _parse_audit(raw):
    """The audit verdict's unsupported claims, or None when the reply is unparseable."""
    try:
        j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        claims = j.get("claims")
        if not isinstance(claims, list):
            return None
        return [str(c.get("claim", "")).strip() for c in claims
                if str(c.get("source", "")).strip().upper() == "UNSUPPORTED"
                and str(c.get("claim", "")).strip()]
    except Exception:
        return None


async def audit_claims(headline, body, sources, errs, title):
    """Cross-model claim validation: the auditor checks the body against its own corpus;
    unsupported claims go back to the main model for ONE surgical revision (re-audited for the
    record only — the revision ships regardless). Returns (headline, body, audit_stamp, info), the
    text possibly revised; the stamp is 'none' when no effective audit happened, and info is
    {verdict (clean|revised|unavailable), unsupported (count), auditor}. Machinery failure ships
    the original: the feed never starves on audit plumbing."""
    from . import pulse
    corpus = _numbered_corpus(sources)

    async def verdict():
        raw, auditor, stamp = await pulse._auditor(
            [{"role": "system", "content": AUDIT_SYS},
             {"role": "user", "content": f"Numbered sources:\n{corpus}\n\nBriefing:\n{body}"}])
        return _parse_audit(raw), auditor, stamp

    from . import editor
    today = datetime.now(TZ).date().isoformat()
    stale = editor.stale_current_claims(body, sources, today)

    try:
        unsupported, auditor, stamp = await verdict()
        parse_failed = unsupported is None
        flagged = list(dict.fromkeys((unsupported or []) + stale))
        if not flagged:
            if parse_failed:
                errs.append(f"claim audit: {title} — audit unavailable (unparseable verdict from {auditor})")
                return headline, body, "none", {"verdict": "unavailable", "unsupported": 0, "auditor": auditor}
            errs.append(f"claim audit: {title} — clean ({auditor})")
            return headline, body, stamp, {"verdict": "clean", "unsupported": 0, "auditor": auditor}
        eff_stamp = stamp if not parse_failed else "date check (verdict unparseable)"
        revised = (await pulse._vera(
            [{"role": "system", "content": REVISE_SYS},
             {"role": "user", "content": ("Unsupported claims:\n- " + "\n- ".join(flagged)
                                          + f"\n\nBriefing:\nHEADLINE: {headline or title}\n\n{body}")}],
            temperature=0.2,
        )).strip()
        new_headline, new_body = _split_headline(revised)
        info = {"verdict": "revised", "unsupported": len(flagged), "auditor": auditor}
        if not new_body:
            errs.append(f"claim audit: {title} — {len(flagged)} unsupported, revision empty; shipped original")
            return headline, body, eff_stamp, info
        stale_note = f", {len(stale)} stale-dated" if stale else ""
        record = f"claim audit: {title} — {len(flagged)} unsupported{stale_note}, revised ({auditor})"
        try:
            body = new_body  # re-audit the revision for the record only
            still, _, _ = await verdict()
            if still:
                record += f"; {len(still)} still flagged"
        except Exception:
            pass
        errs.append(record)
        return (new_headline or headline), new_body, eff_stamp, info
    except Exception as e:
        errs.append(f"claim audit: {title} — audit unavailable ({e})")
        return headline, body, "none", {"verdict": "unavailable", "unsupported": 0, "auditor": None}


async def _audit_hook(url):
    """POST one of the configured audit warm-up/release hooks. The timeout is generous because
    a wake may cold-load a model. A non-2xx reply raises — an error body is JSON too, and a
    failed wake must read as failed, never as success. Returns the response JSON when there is
    any ({} otherwise) — a wake reply may carry {"already_up": true}."""
    async with aiohttp.ClientSession() as s:
        async with s.post(url, timeout=aiohttp.ClientTimeout(total=600)) as r:
            r.raise_for_status()
            try:
                return await r.json()
            except Exception:
                return {}


async def _audit_phase(pending, errs, items_by_card=None):
    """The batched end-of-run claim audit: one optional model wake amortized across every card
    injected this run. `pending` is [(card, full_sources)]. Each card is audited with the same
    audit_claims machinery as the inline path; revisions and the provenance stamp are applied
    to the stored card. The release hook fires only if this run's wake actually started the
    model — a model that was already up belongs to whoever started it.

    `items_by_card`, if given, maps card id -> the run record's structured item, and each card's
    audit verdict is written onto its item so the drill-in can show per-card audit detail."""
    from . import pulse
    if not pending:
        return
    woke = False
    if pulse.AUDIT_WAKE_URL:
        try:
            reply = await pulse._audit_hook(pulse.AUDIT_WAKE_URL)
            woke = not reply.get("already_up")
            errs.append("audit wake: ok" + ("" if woke else " (already up — not ours to release)"))
        except Exception as e:
            errs.append(f"audit wake failed: {e} — auditing via fallback")
    try:
        for card, sources in pending:
            try:
                headline, body, stamp, info = await pulse.audit_claims(
                    card["title"], card["body"], sources, errs, card["title"])
                store.apply_audit(card["id"], headline or card["title"], body, stamp)
                if items_by_card and card["id"] in items_by_card:
                    items_by_card[card["id"]]["audit"] = info
            except Exception as e:
                errs.append(f"claim audit: {card.get('title')} — audit phase error ({e})")
    finally:
        if pulse.AUDIT_RELEASE_URL and woke:
            try:
                await pulse._audit_hook(pulse.AUDIT_RELEASE_URL)
            except Exception as e:
                errs.append(f"audit release failed: {e}")
