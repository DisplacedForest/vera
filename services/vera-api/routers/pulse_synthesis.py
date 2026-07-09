import json
import re
import time

from .persona import voiced
from . import structured
from .websearch import SearchRequest


TRIAGE_SYS = (
    'You\'re planning {who}\'s proactive morning briefing ("Pulse") for {today}. '
    "From their standing interests and what you know about them, pick AT MOST {n} topics "
    "genuinely worth briefing on today. Quality over quantity, and SPREAD: never two topics "
    "serving the same interest. If nothing is genuinely worth surfacing, return an empty "
    "list. No filler. For each topic give a concise web search query that surfaces the "
    "latest on it, and name the standing interest it serves (copied verbatim from the list, "
    "or null when it serves none). Return ONLY JSON: "
    '{{"topics":[{{"title":"short card title","angle":"why it matters today",'
    '"query":"web search query","interest":"the standing interest it serves or null"}}]}}.'
)

TRIAGE_RETRY = (
    "\n\nEverything in the excluded list above is already covered. Do NOT propose a rewording "
    "of any of them — branch instead: an adjacent subject, a different facet of an interest, "
    "or a genuinely new development."
)

_TRIAGE_TEMPS = (0.4, 0.7, 0.9)  # hotter each round to break convergence on the same proposals

THREAD_SYS = (
    "Today is {today}. You're planning how to deepen one Pulse briefing into real research. "
    "From the topic and corpus, identify the 2-4 SPECIFIC threads most worth digging into: "
    "concrete entities, claims, numbers, or people that deserve expansion (e.g. a record transfer fee, "
    "a named signing, a release statistic, a key person). Only include a thread if there is genuinely more "
    "worth knowing; return fewer, or an empty list, if the corpus already covers it. For each thread give a "
    "focused web search query that would surface details and statistics. "
    'Return ONLY JSON: {{"threads":[{{"focus":"what to deepen","query":"search query"}}]}}.'
)

CARD_SYS = (
    "Today is {today}. Write one deep-research Pulse briefing for {who}, in the first person, "
    "like a sharp analyst who knows them.\n"
    "FIRST line of your output: 'HEADLINE: ' followed by a short card headline derived from the "
    "sources. Every person, organization, and competition named in the headline must appear in "
    "the numbered sources verbatim — when the sources disagree with the topic title you were "
    "given, the sources win. Then a blank line, then the briefing.\n"
    "Open the briefing with ONE sentence beginning 'I'm surfacing this because' that says why it matters to them today.\n"
    "Then write the briefing as GitHub-flavored markdown. Expand the key claims with concrete numbers, names, "
    "dates, and context drawn ONLY from the numbered sources. Do not invent facts; people, organizations, and "
    "competition names may be used only as they appear in the sources. Current-state attributions — who "
    "holds a role, who manages, who employs whom — may be stated only when a numbered source establishes "
    "them as current; otherwise leave the holder unnamed. When a claim is notable "
    "(a record, a stat, a named person), say the actual figure or detail rather than gesturing at it.\n"
    "Anchor the briefing in time: state when events happened, prefer the most recent sources when they "
    "conflict, and never present a dated event as current. Every dated event gets an absolute date — "
    "month and year (e.g. 'in January 2025'), never a bare month or 'recently'. If everything in the "
    "sources predates today by a season or more, present the briefing as background or a retrospective, "
    "not as news.\n"
    "End EVERY paragraph with citation references to the sources you used for it, in square brackets: [2] or [1,4].\n"
    "Close with a short 'so what' paragraph: the implication, or what to watch next.\n"
    "Let depth follow the evidence. Write only as many paragraphs as the sources genuinely support (hard ceiling: "
    "9). Never pad to reach a length; a tight 3-paragraph brief beats a padded one.\n"
    "When the material is genuinely quantitative, present it by shape (at most one or two blocks, only when they "
    "beat prose; never decorate): comparing the same metrics across 2+ ENTITIES -> a GitHub-flavored markdown "
    "table; tracking ONE metric across an ordered SEQUENCE (seasons, months, years) -> a chart, not a table. If "
    "you catch yourself listing a metric season-by-season, emit a chart:\n"
    "```vera:chart\n"
    "{{\"type\":\"bar|line|groupedBar\",\"title\":\"...\",\"yLabel\":\"goals\",\"series\":[{{\"name\":\"Openda\",\"points\":[{{\"x\":\"23-24\",\"y\":14}}]}}]}}\n"
    "```\n"
    "OR stat cards for 2-4 headline numbers as a fenced block:\n"
    "```vera:stats\n"
    "{{\"cards\":[{{\"value\":\"33\",\"label\":\"goals\",\"sub\":\"69 games\"}}]}}\n"
    "```\n"
    "Use real values only.\n"
    "{img_instr}"
    "Output only the briefing markdown. No title heading, no separate Sources list (the app renders sources)."
)

SUMMARY_SYS = (
    "Summarize this briefing for a card preview. ONE complete sentence, max 28 words, plain text only "
    "(no markdown, no links, no quotes). It must read as a finished sentence, not a fragment. Output only it."
)


def _numbered_corpus(sources):
    return "\n\n".join(
        f"[{s['n']}] {s['title']}"
        + (f" (published {s['published']})" if s.get("published") else "")
        + f"\nURL: {s['url']}\n{(s.get('content') or '')[:1500]}"
        for s in sources
    )


def _split_headline(body):
    """Pull the 'HEADLINE: ...' first line off a synthesis output. Returns (headline|None, rest).
    A missing or empty headline leaves the body untouched — the caller keeps its working title."""
    m = re.match(r"\s*HEADLINE:\s*(.+?)\s*\n+(.*)", body or "", re.S)
    if not m or not m.group(1).strip():
        return None, body
    return m.group(1).strip(), m.group(2).strip()


def _parse_threads(txt):
    try:
        j = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        th = j.get("threads")
        return th if isinstance(th, list) else []
    except Exception:
        return []


def _source_adder(sources, url_to_n):
    def add_sources(results):
        for x in results:
            u = getattr(x, "url", None)
            if not u or u in url_to_n:
                continue
            n = len(sources) + 1
            url_to_n[u] = n
            sources.append({"n": n, "title": getattr(x, "title", "") or u,
                            "url": u, "content": getattr(x, "content", ""),
                            "published": getattr(x, "published", None)})
    return add_sources


async def _collect_broad_sources(topic, add_sources):
    from . import pulse
    # broad search
    broad = await pulse.web_search(
        SearchRequest(query=topic.get("query") or topic.get("title"), fetch_pages=4, max_results=8)
    )
    add_sources(broad.results)


async def _deepen_sources(topic, sources, add_sources, errs):
    from . import pulse
    # thread extraction — what's worth deepening (may be empty)
    threads = []
    try:
        traw = await pulse._vera(
            [{"role": "system", "content": THREAD_SYS.format(today=time.strftime("%Y-%m-%d"))},
             {"role": "user", "content": f"Topic: {topic.get('title')}\nAngle: {topic.get('angle', '')}\n\n"
                                          f"Corpus:\n{_numbered_corpus(sources)}"}],
            temperature=0.3,
        )
        threads = _parse_threads(traw)[:4]
    except Exception as e:
        errs.append(f"threads {topic.get('title')}: {e}")

    # follow-up searches per thread (fold into master sources)
    for th in threads:
        try:
            fu = await pulse.web_search(
                SearchRequest(query=th.get("query") or th.get("focus") or topic.get("title"),
                              fetch_pages=2, max_results=4)
            )
            add_sources(fu.results)
        except Exception:
            pass
    return threads


def _synthesis_user_prompt(topic, sources, who):
    """The synthesis user message: the topic, its numbered sources, and (when the topic carries
    a Profile Graph seed) the active neighbour nodes the LLM may draw a cross-domain link to."""
    usr = (f"Topic: {topic.get('title')}\nWhy it surfaced: {topic.get('angle', '')}\n\n"
           f"Numbered sources:\n{_numbered_corpus(sources)}")
    seed = topic.get("seed_node_id")
    if seed:
        from . import editor
        usr += editor.connections_block(who, editor.cross_domain_links(seed))
    return usr


async def _synthesize_body(topic, sources, who, inline_images):
    from . import pulse
    # first-person deep synthesis with numbered citations + inline-image tokens
    img_instr = ""
    if inline_images:
        caps = "; ".join(f"{i + 1}: {im['caption']}" for i, im in enumerate(inline_images))
        span = "[[img:1]]" if len(inline_images) == 1 else f"[[img:1]] through [[img:{len(inline_images)}]]"
        img_instr = (f"There are {len(inline_images)} images available. Place each where it best "
                     f"illustrates the text, as a token on its own line: {span}. Use each token at most "
                     f"once. The images show: {caps}.\n")
    card_usr = _synthesis_user_prompt(topic, sources, who)
    body = (
        await pulse._vera(
            [{"role": "system", "content": voiced(CARD_SYS.format(
                img_instr=img_instr, who=who, today=time.strftime("%Y-%m-%d")))},
             {"role": "user", "content": card_usr}],
            temperature=0.5,
        )
    ).strip()
    # The display headline comes from the synthesis (source-grounded); the triage title was
    # only the search plan and may name things that don't exist. Fall back to it if absent.
    headline, body = _split_headline(body)
    return headline, body


async def _summarize(body):
    from . import pulse
    # Short, complete preview blurb (so the card face never truncates mid-word). Generated
    # before cover art so the image prompt can be built from the synthesis.
    try:
        summary = (
            await pulse._vera(
                [{"role": "system", "content": SUMMARY_SYS}, {"role": "user", "content": body[:1500]}],
                temperature=0.3,
            )
        ).strip().strip('"').replace("\n", " ")
    except Exception:
        summary = None
    return summary


async def _select_topics(rnd, *, who, persona, all_interests, memories, exclusions, want,
                         recent_texts=None):
    """The run's topic source. When the Profile Graph has live nodes the selection is the
    Scout -> Analyst pipeline (cheap-rank-first: rank hundreds, keep the top `want`); the
    Analyst delivers its ranked best in one pass, so later rounds yield nothing. With an empty
    graph it falls back to v1 `_triage`, so the feed keeps running until extraction is deployed."""
    from . import pulse
    from . import scout, analyst, editor
    try:
        live = scout.select_live_nodes()
    except Exception:
        live = []
    if live:
        if rnd > 0:
            return []
        found = await scout.scout()
        ranked = await analyst.rank(found.get("candidates", []), recent_card_texts=recent_texts or [],
                                    max_cards=want)
        return editor.survivors_to_topics(ranked.get("chosen", []))
    return await pulse._triage(who, persona, all_interests, memories, exclusions, want, rnd)


async def _triage(who, persona, interests, memories, exclusions, want, rnd):
    """One triage round: propose up to `want` topics, avoiding `exclusions`. Retry rounds
    (rnd > 0) carry an explicit branch-out instruction and a hotter temperature so the model
    stops converging on its favorite proposals."""
    from . import pulse
    usr = (
        (f"About {who}: {persona}\n\n" if persona else "")
        + "Standing interests:\n- "
        + ("\n- ".join(interests) if interests else "(none yet)")
        + "\n\nWhat I know about them (memory):\n- "
        + ("\n- ".join(memories) if memories else "(none)")
        + (("\n\nAlready in the feed (do NOT repeat these — pick different topics):\n- "
            + "\n- ".join(exclusions)) if exclusions else "")
        + (TRIAGE_RETRY if rnd > 0 and exclusions else "")
    )
    msgs = [
        {"role": "system", "content": TRIAGE_SYS.format(today=time.strftime("%Y-%m-%d"), n=want, who=who)},
        {"role": "user", "content": usr},
    ]
    obj, _ = await structured.parsed(
        structured.repairable(pulse._vera, msgs, temperature=_TRIAGE_TEMPS[min(rnd, len(_TRIAGE_TEMPS) - 1)]),
        structured.Topics)
    return ((obj or {}).get("topics") or [])[:want]
