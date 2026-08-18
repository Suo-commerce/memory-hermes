# Generation Timestamp: 2026-08-18T21:40:00Z
# Purpose: RT-13 Layer A (SPEC-REMEMBERING-TOUCH-001 Amendment A5 §2) — pure
#          client-side fidelity classifiers for the Hermes plugin: A1 evidence-
#          use (same turn) and A2 next-turn continuation/correction, plus the
#          POST /v1/memories/fidelity payload builder. No HTTP, no LLM, no
#          provider imports; F2 wires it into __init__.py.
#
# astral-memory/fidelity.py
# Version: 0.1.0 (ships with plugin v2.9.0)
#
# CONTRACT (A5 §2.4 — what this module must not do):
#   - never calls an LLM (regex + set ops only)
#   - never puts memory TEXT in the payload — ids + signals only
#   - never emits a signal for a memory that was not served this turn
#
# STRENGTHS (A5 §2.2/§2.3, unchanged from v2.2 §4.13):
#   A1 STRONG → hit 0.50 | A1 WEAK → hit 0.25 | A1 NONE → no signal
#   A2 NEGATIVE → miss 0.30 | A2 POSITIVE → hit 0.50 | A2 NEUTRAL → nothing
#   Precedence: A2 NEGATIVE overrides A1 STRONG on the same memory (Q2 lean:
#   the earlier hit stands, the miss is added — both are real). Per-memory
#   per-turn hit total capped at 0.50 by the server anyway (positive_strength_cap).
#
# COSINE (A5 §2.3): the plugin has no embedding client, so the ≥0.85 rephrase
#   cosine is replaced by a lexical Jaccard on content words (≥0.60 ⇒ rephrase).
#   The payload carries "cosine_available": false so the server can weight
#   A2 accordingly if it ever wants to.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

_VERSION = "0.1.0"

# ── Strengths ────────────────────────────────────────────────────────────────

A1_STRONG_HIT = 0.50
A1_WEAK_HIT = 0.25
A2_POSITIVE_HIT = 0.50
A2_NEGATIVE_MISS = 0.30

# ── A1 thresholds ────────────────────────────────────────────────────────────

A1_STRONG_OVERLAP = 0.35
A1_WEAK_OVERLAP = 0.15
_MIN_TOKEN_LEN = 4

# ── A2 lexical rephrase threshold (stand-in for cosine ≥ 0.85) ───────────────

A2_REPHRASE_JACCARD = 0.60

# ── Stopwords (EN + FI, small on purpose) ────────────────────────────────────

_STOP = {
    # EN
    "that", "this", "with", "from", "have", "were", "been", "they", "them",
    "their", "there", "then", "than", "what", "when", "where", "which", "while",
    "would", "could", "should", "about", "into", "just", "like", "also", "your",
    "will", "some", "more", "most", "very", "much", "does", "done", "make",
    "made", "here", "over", "only", "such", "each", "other", "these", "those",
    "because", "being", "after", "before", "again", "still", "really", "thing",
    "things", "something", "anything", "using", "used", "user", "assistant",
    # FI
    "että", "mutta", "kanssa", "kuin", "sitten", "tämä", "tuo", "nämä", "nuo",
    "ovat", "olen", "olet", "ollut", "vain", "myös", "koska", "jotta", "kun",
    "mikä", "mitä", "missä", "miten", "joka", "jotka", "sillä", "niin", "kyllä",
    "asia", "asiat", "jotain", "jokin",
}

_WORD_RE = re.compile(r"[a-zA-ZäöåÄÖÅ0-9_\-\./:]+")
_NUMERAL_RE = re.compile(r"\b(?:\d[\d\.,:/-]*\d|\d)\b|\b[A-Za-z]+[-_][A-Za-z0-9\-_]+\b|\bv?\d+\.\d+(?:\.\d+)?\b")

# ── A2 patterns (v2.2 §4.13 EN + A5 §2.3 FI) ─────────────────────────────────

_NEGATIVE_PATTERNS = [
    r"^\s*no[,.!]?\s",
    r"^\s*nope\b",
    r"that'?s (?:not |in)?correct\b",
    r"that'?s wrong\b",
    r"\bnot right\b",
    r"\byou (?:said|told|mentioned|claimed)\b.*\bbut\b",
    r"^\s*actually[,]?\s",
    r"\bi (?:just |already )?asked\b",
    r"\btry again\b",
    r"\bnot what i (?:meant|asked|said)\b",
    r"\bwrong (?:answer|memory|thing)\b",
    r"\bthat isn'?t (?:it|right|what)\b",
    # FI
    r"^\s*ei[,.!]?\s",
    r"^\s*eikä\b",
    r"\bväärin\b",
    r"\bei (?:ole|ollut) oikein\b",
    r"\btarkoitin\b",
    r"\ben tarkoittanut\b",
    r"\byritä uudelleen\b",
    r"\bkysyin jo\b",
]

_POSITIVE_PATTERNS = [
    r"^\s*(?:thanks|thank you|thx|cheers|perfect|great|got it|excellent|nice one)\b",
    r"^\s*(?:ok|okay|alright|cool|nice|good|right)[,.!]?\s",
    r"^\s*(?:yes|yeah|yep|yup|correct|exactly)[,.!]?\s",
    r"\b(?:now|next|also|and then|and)\s+(?:how|what|can|could|show|help|let'?s|please)\b",
    r"^\s*(?:next|now)[,]?\s",
    # FI
    r"^\s*(?:kiitos|kiitti|selvä|ok|okei|hyvä|hienoa|täydellistä|just niin)\b",
    r"^\s*(?:joo|juu|kyllä|niin)[,.!]?\s",
    r"\b(?:seuraavaksi|sitten|entä|voitko|voisitko|näytä)\b",
]

_NEG_RE = [re.compile(p, re.IGNORECASE) for p in _NEGATIVE_PATTERNS]
_POS_RE = [re.compile(p, re.IGNORECASE) for p in _POSITIVE_PATTERNS]

# ── Data types ───────────────────────────────────────────────────────────────


@dataclass
class ServedMemory:
    """One memory that was injected/served this turn. text may be '' when
    only the id is known (prefetch on servers that return ids only) — then
    A1 cannot score it and A2 still can."""
    id: str
    text: str = ""


@dataclass
class FidelitySignal:
    memory_id: str
    signal: str      # "hit" | "miss"
    strength: float
    layer: str       # "A1" | "A2"


@dataclass
class A1Result:
    per_memory: Dict[str, str] = field(default_factory=dict)  # id -> STRONG|WEAK|NONE
    signals: List[FidelitySignal] = field(default_factory=list)


@dataclass
class A2Result:
    verdict: str = "NEUTRAL"   # POSITIVE | NEGATIVE | NEUTRAL
    reason: str = ""           # which rule fired
    signals: List[FidelitySignal] = field(default_factory=list)


# ── Tokenisation ─────────────────────────────────────────────────────────────


def content_tokens(text: str) -> set:
    toks = set()
    for w in _WORD_RE.findall(text or ""):
        w = w.lower().strip(".,:;/-")
        if len(w) >= _MIN_TOKEN_LEN and w not in _STOP:
            toks.add(w)
    return toks


def numeral_tokens(text: str) -> set:
    return {m.lower() for m in _NUMERAL_RE.findall(text or "") if len(m) >= 2}


# ── A1: evidence-use (same turn) ─────────────────────────────────────────────


def classify_evidence_use(memory_text: str, response_text: str) -> Tuple[str, float]:
    """Return (STRONG|WEAK|NONE, overlap). Crude on purpose (A5 §2.2)."""
    mt = content_tokens(memory_text)
    if not mt:
        return "NONE", 0.0
    rt = content_tokens(response_text)
    overlap = len(mt & rt) / len(mt)
    numerals_hit = bool(numeral_tokens(memory_text) & numeral_tokens(response_text))
    if overlap >= A1_STRONG_OVERLAP or numerals_hit:
        return "STRONG", overlap
    if overlap >= A1_WEAK_OVERLAP:
        return "WEAK", overlap
    return "NONE", overlap


def a1_evidence_use(served: Iterable[ServedMemory], response_text: str) -> A1Result:
    out = A1Result()
    if not response_text or not response_text.strip():
        return out
    for m in served:
        if not m.id:
            continue
        if not m.text:
            out.per_memory[m.id] = "UNSCORED"
            continue
        verdict, _ = classify_evidence_use(m.text, response_text)
        out.per_memory[m.id] = verdict
        if verdict == "STRONG":
            out.signals.append(FidelitySignal(m.id, "hit", A1_STRONG_HIT, "A1"))
        elif verdict == "WEAK":
            out.signals.append(FidelitySignal(m.id, "hit", A1_WEAK_HIT, "A1"))
    return out


# ── A2: next-turn continuation / correction ──────────────────────────────────


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def classify_next_turn(next_user_msg: str, prev_user_msg: str) -> Tuple[str, str]:
    """Return (POSITIVE|NEGATIVE|NEUTRAL, reason)."""
    nxt = (next_user_msg or "").strip()
    if not nxt:
        return "NEUTRAL", "empty"
    # Commands / tool-ish messages are neutral.
    if nxt.startswith(("/", "!")):
        return "NEUTRAL", "command"

    for rx in _NEG_RE:
        if rx.search(nxt):
            return "NEGATIVE", f"pattern:{rx.pattern}"

    # Lexical rephrase stand-in for cosine ≥ 0.85.
    j = _jaccard(content_tokens(nxt), content_tokens(prev_user_msg or ""))
    if j >= A2_REPHRASE_JACCARD:
        return "NEGATIVE", f"rephrase:jaccard={j:.2f}"

    for rx in _POS_RE:
        if rx.search(nxt):
            return "POSITIVE", f"pattern:{rx.pattern}"

    # Natural continuation: some lexical continuity but not a rephrase.
    if 0.10 <= j < A2_REPHRASE_JACCARD:
        return "POSITIVE", f"continuation:jaccard={j:.2f}"

    return "NEUTRAL", f"topic_change:jaccard={j:.2f}"


def a2_next_turn(served_ids: Iterable[str], next_user_msg: str,
                 prev_user_msg: str) -> A2Result:
    verdict, reason = classify_next_turn(next_user_msg, prev_user_msg)
    out = A2Result(verdict=verdict, reason=reason)
    ids = [i for i in served_ids if i]
    if verdict == "NEGATIVE":
        out.signals = [FidelitySignal(i, "miss", A2_NEGATIVE_MISS, "A2") for i in ids]
    elif verdict == "POSITIVE":
        out.signals = [FidelitySignal(i, "hit", A2_POSITIVE_HIT, "A2") for i in ids]
    return out


# ── Payload builder (A5 §3.1) ────────────────────────────────────────────────


def build_payload(session_id: str, turn: int, signals: List[FidelitySignal],
                  source: str, max_signals: int = 16,
                  cosine_available: bool = False) -> Optional[dict]:
    """POST body for /v1/memories/fidelity, or None if nothing to send."""
    if not signals:
        return None
    body = {
        "session_id": session_id or "",
        "turn": int(turn or 0),
        "source": source,
        "cosine_available": cosine_available,
        "signals": [
            {"memory_id": s.memory_id, "signal": s.signal,
             "strength": round(float(s.strength), 3), "layer": s.layer}
            for s in signals[:max_signals]
        ],
    }
    return body


# ── Self-tests ───────────────────────────────────────────────────────────────

if __name__ == "__main__":  # python3 fidelity.py
    import sys

    fails = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        global fails
        if not cond:
            fails += 1
            print(f"FAIL {name} {detail}")
        else:
            print(f" ok  {name}")

    # A1
    mem = "The vLLM tool-call delta serialization failure happens on stream chunk 3 with version 0.6.2"
    resp_echo = "Right — the vLLM tool-call delta serialization failure shows up at stream chunk 3 on 0.6.2."
    resp_none = "I checked the calendar; nothing scheduled for Thursday."
    v, ov = classify_evidence_use(mem, resp_echo)
    check("A1 strong on echo", v == "STRONG", f"{v} {ov:.2f}")
    v, ov = classify_evidence_use(mem, resp_none)
    check("A1 none on unrelated", v == "NONE", f"{v} {ov:.2f}")
    v, ov = classify_evidence_use("rate limit is 1000 requests per hour", "Limit: 1000/hr.")
    check("A1 numeral bonus", v == "STRONG", f"{v} {ov:.2f}")
    r = a1_evidence_use([ServedMemory("m1", mem), ServedMemory("m2", ""), ServedMemory("m3", "grocery list eggs milk")], resp_echo)
    check("A1 unscored when no text", r.per_memory.get("m2") == "UNSCORED")
    check("A1 emits only for used", [s.memory_id for s in r.signals] == ["m1"], str(r.signals))

    # A2
    prev = "what's the API rate limit?"
    check("A2 negative: no,", classify_next_turn("No, it's 1000 not 500.", prev)[0] == "NEGATIVE")
    check("A2 negative: actually", classify_next_turn("Actually the limit is 1000", prev)[0] == "NEGATIVE")
    check("A2 negative: rephrase", classify_next_turn("what is the rate limit of the API?", prev)[0] == "NEGATIVE",
          classify_next_turn("what is the rate limit of the API?", prev)[1])
    check("A2 positive: thanks", classify_next_turn("Thanks, now how do I handle rate limit errors?", prev)[0] == "POSITIVE")
    check("A2 positive: continuation", classify_next_turn("great, and what about the retry backoff for rate limit?", prev)[0] == "POSITIVE")
    check("A2 neutral: command", classify_next_turn("/new", prev)[0] == "NEUTRAL")
    check("A2 neutral: topic change", classify_next_turn("book me a dentist for tuesday", prev)[0] == "NEUTRAL",
          classify_next_turn("book me a dentist for tuesday", prev)[1])
    check("A2 FI negative", classify_next_turn("Ei, tarkoitin toista bugia", prev)[0] == "NEGATIVE")
    check("A2 FI positive", classify_next_turn("Kiitos, entä seuraavaksi?", prev)[0] == "POSITIVE")
    # The known hard case from v2.2 risk register: refinement, not correction.
    v, why = classify_next_turn("no, show me more of those", prev)
    print(f"      note: refinement case classified {v} ({why}) — expected NEGATIVE by design; A5 §6 gate measures this")

    # Signals + payload
    a2 = a2_next_turn(["m1", "m9"], "No, that's wrong", prev)
    check("A2 miss strength", all(s.signal == "miss" and s.strength == A2_NEGATIVE_MISS for s in a2.signals))
    p = build_payload("sess-1", 7, r.signals + a2.signals, "hermes-2.9.0")
    check("payload shape", p is not None and set(p) == {"session_id", "turn", "source", "cosine_available", "signals"})
    check("payload has no text", "text" not in str(p) and mem not in str(p))
    check("payload none when empty", build_payload("s", 1, [], "x") is None)

    print("\nfidelity.py self-test:", "PASS" if fails == 0 else f"{fails} FAIL")
    sys.exit(1 if fails else 0)
