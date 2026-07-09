import re
import time

from . import pulse_store as store


def _recent_for_user(user_id, days=7):
    """Active cards + anything injected in the last `days`, deduped by id. The corpus the
    dedup gate checks a candidate against — so 'I did this yesterday' still counts after it sweeps."""
    cutoff = int(time.time()) - days * 86400
    active = store.list_cards(include_expired=False, user_id=user_id)
    recent = [c for c in store.list_cards(include_expired=True, user_id=user_id)
              if (c.get("created_at") or 0) >= cutoff]
    seen, out = set(), []
    for c in active + recent:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        out.append(c)
    return out


DEDUP_SYS = (
    "You are deduplicating a research feed. Given a CANDIDATE topic and a numbered list of cards "
    "ALREADY in the feed, decide whether any existing card already covers the same thing — same "
    "subject AND claim. Reworded titles still count as the same (e.g. 'New study on X' is the same "
    "as 'Recent findings on X'). A genuinely different angle on a shared interest is NOT the same. "
    "Reply with ONLY 'YES <n>' naming the matching card number, or 'NO'."
)


async def already_covered(topic, user_id):
    """Has Vera already produced a card for this candidate? Returns the matching card dict,
    or None. The deterministic dedup gate — semantic (catches re-wording), per-user, fail-open."""
    from . import pulse
    cards = pulse._recent_for_user(user_id)
    if not cards:
        return None
    listing = "\n".join(f"{i + 1}. {c['title']} — {(c.get('summary') or '')[:200]}"
                        for i, c in enumerate(cards))
    cand = f"{topic.get('title')} | {topic.get('angle', '')} | {topic.get('query', '')}"
    try:
        raw = await pulse._vera(
            [{"role": "system", "content": DEDUP_SYS},
             {"role": "user", "content": f"CANDIDATE: {cand}\n\nEXISTING:\n{listing}"}],
            temperature=0.0,
        )
        m = re.match(r"\s*YES\s+(\d+)", raw or "", re.I)
        if m and 1 <= int(m.group(1)) <= len(cards):
            return cards[int(m.group(1)) - 1]
    except Exception:
        return None  # fail-open: a gate error must never silently suppress all research
    return None


FRESH_SYS = (
    "Today is {today}. You judge whether a Pulse briefing topic is STALE NEWS: a time-sensitive "
    "news topic (a signing, a release, an announcement, a result) whose newest available coverage "
    "is too old for the event to still be briefed as news today. Evergreen subjects — research "
    "findings, techniques, background, analysis of standing situations — are NEVER stale, whatever "
    "their source age. Answer ONLY the single word STALE or FRESH."
)


def _newest_published(sources):
    """The newest publish date (YYYY-MM-DD) across the corpus, or None when nothing is dated."""
    return max((s["published"] for s in sources if s.get("published")), default=None)


COHERENT_SYS = (
    "You check whether a research corpus actually covers a proposed briefing topic. Adjacent "
    "context is fine — coverage of the topic's club, field, or surrounding situation counts. "
    "The bar is whether the corpus is SUBSTANTIALLY about a different subject than the topic. "
    'Answer ONLY one line: "ON-TOPIC" or "OFF-TOPIC <what the corpus is actually about>".'
)


def _corpus_overview(sources, chars=200):
    """The corpus as numbered titles + snippet heads — enough for a subject check, not a read."""
    return "\n".join(f"[{s['n']}] {s['title']}: {(s.get('content') or '')[:chars]}" for s in sources)


async def is_off_topic(topic, sources):
    """The coherence gate — (True, what the corpus is about) when the broad search drifted to a
    different subject than the topic; (False, None) otherwise. Fail-open: an error can never
    suppress research."""
    from . import pulse
    try:
        raw = await pulse._vera(
            [{"role": "system", "content": COHERENT_SYS},
             {"role": "user", "content": (f"Topic: {topic.get('title')}\n"
                                          f"Angle: {topic.get('angle', '')}\n\n"
                                          f"Corpus:\n{_corpus_overview(sources)}")}],
            temperature=0.0,
        )
        m = re.match(r"\s*OFF-TOPIC\b[:\s]*(.*)", raw or "", re.I)
        if m:
            return True, (m.group(1).strip() or "a different subject")
    except Exception:
        pass
    return False, None


async def is_stale_news(topic, newest):
    """The freshness gate — True only when the topic is time-sensitive news whose newest coverage
    (`newest`, a YYYY-MM-DD string or None) is too old to brief as news today. An undated corpus
    passes without consulting the model: with no date evidence there is nothing to judge, and
    engines that omit dates must never read as staleness. Fail-open: an error or an unparseable
    verdict can never suppress research."""
    from . import pulse
    if not newest:
        return False
    try:
        raw = await pulse._vera(
            [{"role": "system", "content": FRESH_SYS.format(today=time.strftime("%Y-%m-%d"))},
             {"role": "user", "content": (f"Topic: {topic.get('title')}\n"
                                          f"Angle: {topic.get('angle', '')}\n"
                                          f"Newest source date: {newest or 'no dated sources'}")}],
            temperature=0.0,
        )
        return bool(re.match(r"\s*STALE\b", raw or "", re.I))
    except Exception:
        return False


# Skip-marker prefix -> gate name, for the per-run kill tally that makes a starved run
# (gates ate the proposals) distinguishable from a quiet news day (triage had nothing).
_GATE_MARKERS = (
    ("skipped (already covered)", "dedup"),
    ("skipped (stale news)", "freshness"),
    ("skipped (off-topic corpus)", "coherence"),
    ("skipped (empty synthesis)", "empty"),
)


def _gate_kind(new_errors):
    for e in new_errors:
        for prefix, kind in _GATE_MARKERS:
            if e.startswith(prefix):
                return kind
    return "empty"
