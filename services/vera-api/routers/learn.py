"""The feedback loop — observe -> weight -> research -> react -> learn.

Two jobs, both math. **Write-back** turns each reaction on a card into a deterministic
engagement adjustment on the graph node(s) the card served (a thumb up lifts the topic, a
thumb down suppresses it). **The fit** treats those same reactions as a labeled set and fits a
transparent logistic regression over the five ranking features, installing the learned
coefficients as the Analyst's weights once enough samples accrue. All constants are declared
and env-tunable.
"""
import math
import os
import time

from . import learn_store as ls
from . import profile_graph_store as pg


def _envf(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


UP_BONUS = _envf("PULSE_UP_BONUS", "2.0")            # thumb up: engagement += this
DOWN_DECAY = _envf("PULSE_DOWN_DECAY", "0.5")        # thumb down: engagement *= this
OPEN_BONUS = _envf("PULSE_OPEN_BONUS", "1.0")        # opened/promoted: graded positive
BOOKMARK_BONUS = _envf("PULSE_BOOKMARK_BONUS", "1.5")
EXPIRE_PENALTY = _envf("PULSE_EXPIRE_PENALTY", "0.9")  # ignored -> expired: mild multiplicative
DISCUSSED_BONUS = _envf("PULSE_DISCUSSED_BONUS", "3.0")  # topic returns in chat: strong positive
EDGE_REINFORCE = _envf("PULSE_EDGE_REINFORCE", "0.5")   # thumb up: traversed edge weight += this

_ADD = {"up": UP_BONUS, "open": OPEN_BONUS, "opened": OPEN_BONUS, "promote": OPEN_BONUS,
        "promoted": OPEN_BONUS, "bookmark": BOOKMARK_BONUS, "bookmarked": BOOKMARK_BONUS}
_SCALE = {"down": DOWN_DECAY, "expire": EXPIRE_PENALTY, "expired": EXPIRE_PENALTY,
          "ignore": EXPIRE_PENALTY, "ignored": EXPIRE_PENALTY}


def _adjust(nid, now, add=0.0, factor=1.0):
    """Decay a node's engagement to `now`, then scale and offset it. Engagement floors at 0."""
    node = pg.get_node(nid)
    if not node:
        return
    new = pg.engagement_now(node, now) * factor + add
    pg.upsert_node(id=nid, type=node["type"], label=node["label"], aliases=node["aliases"],
                   facts=node["facts"], engagement=max(0.0, new), last_engaged=now,
                   state=node["state"], resolve_condition=node["resolve_condition"],
                   next_check=node["next_check"], confidence=node["confidence"],
                   embedding=node["embedding"])


def apply_signal(card_id, signal, now=None):
    """Write one reaction back to the graph: adjust the served node(s)' engagement by the
    signal's effect and record the outcome for the fit. A card with no recorded link is a
    no-op. Returns the count of nodes adjusted."""
    now = int(time.time()) if now is None else now
    link = ls.link(card_id)
    if not link:
        return {"nodes": 0}
    for nid in link["nodes"]:
        if signal in _ADD:
            _adjust(nid, now, add=_ADD[signal])
            if signal == "up":
                for e in pg.neighbors(nid):
                    pg.add_edge(e["src_id"], e["dst_id"], e["type"],
                                weight=(e["weight"] or 1.0) + EDGE_REINFORCE)
        elif signal in _SCALE:
            _adjust(nid, now, factor=_SCALE[signal])
    ls.record_outcome(card_id, signal, now)
    return {"nodes": len(link["nodes"])}


def reinforce_node(node_id, now=None, factor=1.0):
    """Strong-positive reinforcement when a topic returns in chat (the extraction job detects
    the node re-observed): engagement += DISCUSSED_BONUS × factor."""
    now = int(time.time()) if now is None else now
    _adjust(node_id, now, add=DISCUSSED_BONUS * factor)


# --------------------------------------------------------------------------- learned-weight fit

FIT_MIN_SAMPLES = int(_envf("PULSE_FIT_MIN_SAMPLES", "200"))   # labeled examples before learning
FIT_ITERS = int(_envf("PULSE_FIT_ITERS", "800"))
FIT_LR = _envf("PULSE_FIT_LR", "0.5")


def _sigmoid(z):
    if z < -60:
        return 0.0
    if z > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def logistic_fit(X, y, iters=None, lr=None):
    """Batch-gradient-descent logistic regression. Returns `(weights, bias)` over the columns of
    X. Pure Python, no dependency; the model is five coefficients, fully inspectable."""
    iters = FIT_ITERS if iters is None else iters
    lr = FIT_LR if lr is None else lr
    m = len(X)
    k = len(X[0]) if m else 0
    w = [0.0] * k
    b = 0.0
    for _ in range(iters):
        gw = [0.0] * k
        gb = 0.0
        for xi, yi in zip(X, y):
            p = _sigmoid(b + sum(w[j] * xi[j] for j in range(k)))
            err = p - yi
            for j in range(k):
                gw[j] += err * xi[j]
            gb += err
        for j in range(k):
            w[j] -= lr * gw[j] / m
        b -= lr * gb / m
    return w, b


def fit_weights(min_samples=None, now=None):
    """The data-science endgame: fit logistic regression over the five ranking features against
    the recorded outcomes, normalize the non-negative coefficients to sum to 1, and install them
    as the Analyst's weights. No-ops below `min_samples` (the hand-set weights stand) or when the
    fit is degenerate. Returns the installed coefficients, or None."""
    now = int(time.time()) if now is None else now
    threshold = FIT_MIN_SAMPLES if min_samples is None else min_samples
    examples = ls.labeled_examples()
    if len(examples) < threshold:
        return None
    keys = ls.FEATURE_KEYS
    X = [[feat[k] for k in keys] for feat, _ in examples]
    y = [label for _, label in examples]
    w, _ = logistic_fit(X, y)
    nonneg = [max(0.0, c) for c in w]
    total = sum(nonneg)
    if total <= 0:
        return None
    coeffs = {k: nonneg[i] / total for i, k in enumerate(keys)}
    ls.set_weights(coeffs, n_samples=len(examples), now=now)
    return coeffs
