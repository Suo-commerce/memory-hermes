#!/usr/bin/env python3
# Generated: 2026-09-01
# Purpose: Distillation engine v1.2.0 — Phase-1 AC-12 fixes after the first
#          live pass on bot-jarmo replaced curated context with session churn:
#          (1) pinned/distilled sections — the distiller AUGMENTS a bounded
#          block and never touches human-written text; (2) tier preference
#          inverted toward consolidated (slow/medium) memories; (3) entry
#          hygiene — length cap, artifact stripping, meta/status filters,
#          dedup against pinned text; (4) USER.md identity facts implemented.
"""
astral_distillation.py — v1.2.0

Derives MEMORY.md / USER.md (and CLAUDE.md / AGENTS.md) as computed views of
Astral Core Memory. Verified against the live server 2026-08-31:

  - Memories:    POST /v1/memory/search  -> {"results": [{similarity,
                 surprise_score, utility_score, source_role, created_at,
                 speed, category, access_count, content_class, text, id}]}
  - Preferences: POST /v1/memory/list {"namespace": "preferences"} ->
                 {"memories": [...]}, active = metadata.status == "active"
                 (same path the plugin's astral_preferences tool uses; if the
                 server 405s this route, prefs degrade gracefully to empty)
  - Loop guard:  module flag + content marker; on_memory_write must check
                 `distillation_in_progress()` OR the DISTILL_MARKER literal
                 and skip /v1/memory/ingest (Invariant DL-1).

Run `python3 astral_distillation.py --selftest` for the test suite,
or with no args for a manual one-shot pass (SPEC §10 Phase 1).
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

__version__ = "1.2.0"
DISTILL_MARKER = f"[astral-distillation v{__version__}]"  # loop-guard literal

# ---------------------------------------------------------------------------
# Loop guard (SPEC v1.1 §4.6, Invariant DL-1) — importable by the plugin's
# on_memory_write patch: `from astral_distillation import distillation_in_progress`
# ---------------------------------------------------------------------------

_GUARD = threading.Event()


def distillation_in_progress() -> bool:
    return _GUARD.is_set()


def content_is_distilled(content: str) -> bool:
    """Marker check — second guard layer, survives cross-process writes."""
    return "[astral-distillation v" in (content or "")


# ---------------------------------------------------------------------------
# Constants (SPEC v1.1 §2, §4.2)
# ---------------------------------------------------------------------------

PROVENANCE_WEIGHTS = {"user": 1.0, "dyad": 0.8, "assistant": 0.5, "diagnostic": 0.1}
PROVENANCE_RANK = {"user": 3, "dyad": 2, "assistant": 1, "diagnostic": 0}
# v1.2: INVERTED. Consolidated tiers hold the durable facts a standing-context
# file wants; fast tier is where session churn lives (AC-12 finding, 2026-09-01).
TIER_WEIGHTS = {"slow": 1.0, "medium": 0.9, "fast": 0.5, "dormant": 0.3}

DEFAULT_MEMORY_BUDGET = 2200
DEFAULT_USER_BUDGET = 1375
DEFAULT_CACHE_TTL = 300
DEFAULT_TIMEOUT = 5
DEFAULT_HALF_LIFE_DAYS = 365.0   # v1.2: was 30 — recency must not dominate durability
DEFAULT_ENTRY_MAX_CHARS = 180    # v1.2: entries longer than this are skipped, not truncated
DEFAULT_MEMORY_DISTILLED_BUDGET = 600   # v1.2: machine-owned block inside MEMORY.md
DEFAULT_USER_DISTILLED_BUDGET = 400     # v1.2: machine-owned block inside USER.md
DISTILLED_BEGIN = "<!-- astral:distilled:begin {marker} — auto-generated, do not edit -->"
DISTILLED_END = "<!-- astral:distilled:end -->"
_BEGIN_RE = re.compile(r"<!-- astral:distilled:begin .*? -->\n?", re.S)
_END_RE = re.compile(r"<!-- astral:distilled:end -->\n?")
_ARTIFACT_RE = re.compile(r"\s*\[DATES MENTIONED:[^\]]*\]", re.I)
_META_RE = re.compile(
    r"(stored in memory|memory system|MEMORY\.md|USER\.md|distill|astral_store|"
    r"the assistant needs|the assistant should)", re.I)
_STATUS_RE = re.compile(
    r"(status at \d|\d+\s*(of|/)\s*\d+\s*(scenarios|completed|done)|in progress|"
    r"at \d+ minutes|on track for|so far:|test run)", re.I)

# SPEC v1.1 §4.2a — canonical coverage queries (global-importance proxy)
DEFAULT_COVERAGE_QUERIES = [
    "user identity preferences habits",
    "current projects work status",
    "environment tools infrastructure",
    "decisions principles learnings",
    "relationships collaborators contacts",
]
DEFAULT_PER_QUERY_LIMIT = 10


# ---------------------------------------------------------------------------
# Data model — mirrors verified /v1/memory/search result fields
# ---------------------------------------------------------------------------

@dataclass
class Memory:
    id: str
    text: str
    source_role: str = "assistant"      # user | dyad | assistant | diagnostic
    similarity: float = 0.0             # per-query; max kept across queries
    surprise_score: float = 0.5
    utility_score: float = 0.5
    created_at: Optional[str] = None    # ISO 8601
    speed: str = "fast"                 # fast | medium | slow | dormant
    category: str = "fact"
    access_count: int = 0
    content_class: str = "payload"      # "noise" is filtered out

    def age_days(self, now: Optional[datetime] = None) -> float:
        if not self.created_at:
            return 0.0
        try:
            ts = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - ts).total_seconds() / 86400.0)

    def score(self, half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
              now: Optional[datetime] = None) -> float:
        recency = math.exp(-self.age_days(now) / half_life_days)
        prov = PROVENANCE_WEIGHTS.get(self.source_role, 0.5)
        tier = TIER_WEIGHTS.get(self.speed, 0.4)
        # v1.2: access_count promoted from tiebreaker to signal — the store
        # already knows which facts get recalled constantly.
        popularity = 1.0 + math.log1p(max(0, self.access_count)) / 4.0
        return (self.similarity * self.utility_score * self.surprise_score
                * recency * prov * tier * popularity)


@dataclass
class Preference:
    id: str
    text: str
    status: str = "active"   # active | pending | declined | withdrawn


@dataclass
class DistillationResult:
    wrote: bool
    files: dict = field(default_factory=dict)
    diffs: dict = field(default_factory=dict)
    warning: Optional[str] = None
    fetched: int = 0
    selected: int = 0


# ---------------------------------------------------------------------------
# Astral Core client — matches plugin _HttpClient behavior (Bearer auth,
# token from astral-memory.json `api_token` or ASTRAL_API_TOKEN env)
# ---------------------------------------------------------------------------

class AstralClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8090",
                 api_token: Optional[str] = None,
                 hermes_home: Optional[Path] = None,
                 timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_token = (api_token
                          or os.environ.get("ASTRAL_API_TOKEN", "")
                          or self._token_from_config(hermes_home))

    @staticmethod
    def _token_from_config(hermes_home: Optional[Path]) -> str:
        cfg_path = (hermes_home or Path.home() / ".hermes") / "astral-memory.json"
        if cfg_path.exists():
            try:
                return str(json.loads(cfg_path.read_text(encoding="utf-8"))
                           .get("api_token", "") or "")
            except (json.JSONDecodeError, OSError):
                pass
        return ""

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_token:
            req.add_header("Authorization", f"Bearer {self.api_token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # -- memories: multi-query coverage (SPEC v1.1 §4.2a) -------------------
    def fetch_memories(self,
                       queries: list = None,
                       per_query_limit: int = DEFAULT_PER_QUERY_LIMIT,
                       namespace_filter: Optional[str] = None) -> list:
        queries = queries or DEFAULT_COVERAGE_QUERIES
        by_id: dict = {}
        for q in queries:
            payload = {"query": q, "limit": per_query_limit}
            if namespace_filter:
                payload["namespace_filter"] = namespace_filter
            data = self._post("/v1/memory/search", payload)
            for r in data.get("results", []) or []:
                m = self._parse_memory(r)
                if not m.id:
                    continue
                prev = by_id.get(m.id)
                if prev is None or m.similarity > prev.similarity:
                    by_id[m.id] = m      # keep max similarity across queries
        return list(by_id.values())

    # -- preferences via /v1/memory/list (plugin-verified path) -------------
    def fetch_active_preferences(self) -> list:
        try:
            data = self._post("/v1/memory/list",
                              {"namespace": "preferences", "limit": 50})
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            return []   # 405 or unreachable: prefs degrade to empty, not fatal
        if "error" in data:
            return []
        out = []
        for m in data.get("memories", []) or []:
            status = (m.get("metadata") or {}).get("status", "")
            if status == "active":
                out.append(Preference(id=str(m.get("id", "")),
                                      text=m.get("text", ""),
                                      status="active"))
        return out

    @staticmethod
    def _parse_memory(r: dict) -> Memory:
        return Memory(
            id=str(r.get("id", "")),
            text=r.get("text", r.get("content", "")),
            source_role=str(r.get("source_role", "assistant")).lower(),
            similarity=float(r.get("similarity", 0.0)),
            surprise_score=float(r.get("surprise_score", 0.5)),
            utility_score=float(r.get("utility_score", 0.5)),
            created_at=r.get("created_at"),
            speed=str(r.get("speed", "fast")).lower(),
            category=str(r.get("category", "fact")).lower(),
            access_count=int(r.get("access_count", 0)),
            content_class=str(r.get("content_class", "payload")).lower(),
        )


# ---------------------------------------------------------------------------
# Filtering / conflict resolution (SPEC v1.1 §4.2, §8)
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """v1.2: strip extractor artifacts and collapse whitespace."""
    return " ".join(_ARTIFACT_RE.sub("", text or "").split())


def eligible(m: Memory, max_chars: int = DEFAULT_ENTRY_MAX_CHARS) -> bool:
    """v1.2 hygiene: noise/diagnostic/context out; long, meta, and
    status-report entries out. All tiers eligible (weights decide)."""
    if m.content_class == "noise":
        return False
    if m.category in ("diagnostic", "context"):
        return False
    text = clean_text(m.text)
    if not text or len(text) > max_chars:
        return False
    if _META_RE.search(text) or _STATUS_RE.search(text):
        return False
    return True


def identity_fact(m: Memory) -> bool:
    """USER.md distilled block: what the user said about themself."""
    return m.source_role == "user" and m.category == "fact"


def not_in_pinned(m_text: str, pinned: str, threshold: float = 0.5) -> bool:
    """Skip entries the human already wrote (Jaccard vs each pinned line)."""
    tm = _norm_tokens(m_text)
    if not tm:
        return False
    for line in pinned.splitlines():
        tl = _norm_tokens(line)
        if tl and len(tm & tl) / len(tm | tl) >= threshold:
            return False
    return True


def _norm_tokens(text: str) -> set:
    return {t for t in "".join(c.lower() if c.isalnum() else " "
                               for c in text).split() if len(t) > 3}


def same_topic(a: Memory, b: Memory, threshold: float = 0.6) -> bool:
    """OQ-6 Phase-1 heuristic: normalized token overlap (Jaccard)."""
    ta, tb = _norm_tokens(a.text), _norm_tokens(b.text)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def resolve_conflicts(memories: list) -> list:
    """Same-topic: higher provenance rank wins; tie -> higher score."""
    kept: list = []
    for m in sorted(memories,
                    key=lambda x: (PROVENANCE_RANK.get(x.source_role, 0),
                                   x.score()),
                    reverse=True):
        if not any(same_topic(m, k) for k in kept):
            kept.append(m)
    return kept


def select_top_k(memories: list, budget: int,
                 fmt_len: Callable) -> list:
    chosen, used = [], 0
    for m in sorted(memories,
                    key=lambda x: (x.score(), x.access_count), reverse=True):
        cost = fmt_len(m)
        if used + cost <= budget:
            chosen.append(m)
            used += cost
    return chosen


# ---------------------------------------------------------------------------
# v1.2 sections — pinned (human-owned, never touched) vs distilled (bounded)
# ---------------------------------------------------------------------------

def split_sections(content: str) -> tuple:
    """Return (pinned, old_distilled). Pinned is everything outside the
    delimited block, byte-preserved. First run: pinned == whole file."""
    b = _BEGIN_RE.search(content or "")
    if not b:
        return (content or ""), ""
    e = _END_RE.search(content, b.end())
    if not e:  # unterminated block: treat everything after begin as ours
        return content[:b.start()], content[b.end():]
    return content[:b.start()] + content[e.end():], content[b.end():e.start()]


def join_sections(pinned: str, distilled: str) -> str:
    if not distilled.strip():
        return pinned
    sep = "" if (not pinned or pinned.endswith("\n")) else "\n"
    return (pinned + sep
            + DISTILLED_BEGIN.format(marker=DISTILL_MARKER) + "\n"
            + distilled.rstrip("\n") + "\n"
            + DISTILLED_END + "\n")


# ---------------------------------------------------------------------------
# Adapters (SPEC §6.1) — corrected paths: ~/.hermes/memories/
# ---------------------------------------------------------------------------

class DistillationAdapter:
    def get_context_files(self) -> list: raise NotImplementedError
    def get_char_budget(self, file_path: Path) -> int: raise NotImplementedError
    def entry_len(self, m: Memory) -> int: raise NotImplementedError
    def format_memories(self, memories: list, file_path: Path) -> str:
        raise NotImplementedError
    def format_preferences(self, prefs: list, file_path: Path) -> str:
        raise NotImplementedError

    def write(self, file_path: Path, content: str) -> None:
        """Atomic write under the loop guard (DL-1)."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(file_path.parent), suffix=".tmp")
        _GUARD.set()
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, file_path)
        finally:
            _GUARD.clear()
            if os.path.exists(tmp):
                os.unlink(tmp)


class HermesAdapter(DistillationAdapter):
    """MEMORY.md / USER.md under ~/.hermes/memories/ (verified path)."""

    def __init__(self, hermes_home: Optional[Path] = None,
                 profile: Optional[str] = None,
                 memory_budget: int = DEFAULT_MEMORY_BUDGET,
                 user_budget: int = DEFAULT_USER_BUDGET,
                 memory_distilled_budget: int = DEFAULT_MEMORY_DISTILLED_BUDGET,
                 user_distilled_budget: int = DEFAULT_USER_DISTILLED_BUDGET):
        base = hermes_home or (Path.home() / ".hermes")
        if profile and profile != "default":
            base = base / "profiles" / profile
        mem_dir = base / "memories"
        self.memory_md = mem_dir / "MEMORY.md"
        self.user_md = mem_dir / "USER.md"
        self._budgets = {self.memory_md: memory_budget, self.user_md: user_budget}
        self._distilled = {self.memory_md: memory_distilled_budget,
                           self.user_md: user_distilled_budget}

    def get_distilled_budget(self, file_path: Path) -> int:
        return self._distilled[file_path]

    def get_context_files(self) -> list:
        return [self.memory_md, self.user_md]

    def get_char_budget(self, file_path: Path) -> int:
        return self._budgets[file_path]

    @staticmethod
    def _fmt_entry(m: Memory) -> str:
        return f"\u00a7 [{m.source_role}] {clean_text(m.text)}\n"

    def entry_len(self, m: Memory) -> int:
        return len(self._fmt_entry(m))

    def format_memories(self, memories: list, file_path: Path) -> str:
        # v1.2: block body only — delimiters are added by join_sections
        return "".join(self._fmt_entry(m) for m in memories)

    def format_preferences(self, prefs: list, file_path: Path,
                           identity: Optional[list] = None) -> str:
        """v1.2: preferences + identity facts (SPEC §4.3 USER.md mapping)."""
        out = []
        active = [p for p in prefs if p.status == "active"]
        if active:
            out.append("[PREFERENCES \u2014 learned, consented]")
            out += [f"- {clean_text(p.text)}" for p in active]
            out.append("[/PREFERENCES]")
        if identity:
            out.append("[IDENTITY \u2014 stated by user]")
            out += [f"- {clean_text(m.text)}" for m in identity]
            out.append("[/IDENTITY]")
        return ("\n".join(out) + "\n") if out else ""


class GenericMarkdownAdapter(DistillationAdapter):
    """Single-file agents: CLAUDE.md (Claude Code), AGENTS.md (Codex)."""

    def __init__(self, target: Path, budget: int = DEFAULT_MEMORY_BUDGET):
        self.target = target
        self.budget = budget

    def get_context_files(self) -> list:
        return [self.target]

    def get_char_budget(self, file_path: Path) -> int:
        return self.budget

    def get_distilled_budget(self, file_path: Path) -> int:
        return self.budget

    def entry_len(self, m: Memory) -> int:
        return len(f"- [{m.source_role}] {clean_text(m.text)}\n")

    def format_memories(self, memories: list, file_path: Path) -> str:
        return "".join(f"- [{m.source_role}] {clean_text(m.text)}\n"
                       for m in memories)

    def format_preferences(self, prefs: list, file_path: Path,
                           identity: Optional[list] = None) -> str:
        active = [p for p in prefs if p.status == "active"]
        if not active:
            return ""
        return ("\n### Preferences (user-approved)\n"
                + "".join(f"- {p.text}\n" for p in active))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class DistillationEngine:
    def __init__(self, client, adapter: HermesAdapter,
                 cache_ttl: int = DEFAULT_CACHE_TTL,
                 log_path: Optional[Path] = None,
                 coverage_queries: Optional[list] = None,
                 per_query_limit: int = DEFAULT_PER_QUERY_LIMIT,
                 write_approval: bool = False,
                 approval_callback: Optional[Callable] = None):
        self.client = client
        self.adapter = adapter
        self.cache_ttl = cache_ttl
        self.log_path = log_path or (Path.home() / ".hermes" / "logs"
                                     / "distillation.log")
        self.coverage_queries = coverage_queries or DEFAULT_COVERAGE_QUERIES
        self.per_query_limit = per_query_limit
        self.write_approval = write_approval
        self.approval_callback = approval_callback
        self._last_run = 0.0

    @staticmethod
    def _entry_set(content: str) -> set:
        return {ln.strip() for ln in content.splitlines()
                if ln.strip() and not ln.startswith("#")}

    def _log_diff(self, path: Path, old: str, new: str) -> dict:
        diff = {"added": sorted(self._entry_set(new) - self._entry_set(old)),
                "removed": sorted(self._entry_set(old) - self._entry_set(new))}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "marker": DISTILL_MARKER, "file": str(path), **diff,
            }) + "\n")
        return diff

    def run(self, force: bool = False) -> DistillationResult:
        if not force and (time.time() - self._last_run) < self.cache_ttl:
            return DistillationResult(wrote=False, warning="cache_fresh_skip")

        try:
            memories = self.client.fetch_memories(
                self.coverage_queries, self.per_query_limit)
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                TimeoutError) as e:
            return DistillationResult(
                wrote=False, warning=f"astral_unreachable_keep_existing: {e}")
        preferences = self.client.fetch_active_preferences()  # fail-soft

        mem_file, user_file = self.adapter.get_context_files()
        old_mem = mem_file.read_text(encoding="utf-8") if mem_file.exists() else ""
        old_user = user_file.read_text(encoding="utf-8") if user_file.exists() else ""
        pinned_mem, _ = split_sections(old_mem)
        pinned_user, _ = split_sections(old_user)

        # v1.2: hygiene first, then skip what the human already wrote
        clean = [m for m in memories
                 if eligible(m) and not_in_pinned(clean_text(m.text), pinned_mem)]
        keep = resolve_conflicts(clean)

        # Distilled block budget = min(configured block budget,
        #                             file budget - pinned - delimiters)
        overhead = len(DISTILLED_BEGIN.format(marker=DISTILL_MARKER)) + len(DISTILLED_END) + 3
        mem_room = min(self.adapter.get_distilled_budget(mem_file),
                       self.adapter.get_char_budget(mem_file) - len(pinned_mem) - overhead)
        selected = select_top_k(keep, max(0, mem_room), self.adapter.entry_len) \
            if mem_room > 0 else []
        selected.sort(key=lambda m: m.score(), reverse=True)
        memory_content = join_sections(
            pinned_mem, self.adapter.format_memories(selected, mem_file))

        # USER.md: preferences + identity facts, deduped against pinned
        prefs = [p for p in preferences
                 if not_in_pinned(clean_text(p.text), pinned_user)]
        identity = [m for m in memories
                    if identity_fact(m) and eligible(m)
                    and not_in_pinned(clean_text(m.text), pinned_user)]
        identity.sort(key=lambda m: m.score(), reverse=True)
        user_room = min(self.adapter.get_distilled_budget(user_file),
                        self.adapter.get_char_budget(user_file) - len(pinned_user) - overhead)
        user_block = ""
        if user_room > 0:
            while True:
                user_block = self.adapter.format_preferences(prefs, user_file, identity)
                if len(user_block) <= user_room or not (identity or prefs):
                    break
                if identity:
                    identity.pop()      # drop lowest-scoring identity fact first
                else:
                    prefs.pop()
        user_content = join_sections(pinned_user, user_block)

        result = DistillationResult(wrote=False, fetched=len(memories),
                                    selected=len(selected))
        for path, new in ((mem_file, memory_content), (user_file, user_content)):
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            if old == new:
                continue
            if self.write_approval and self.approval_callback:
                if not self.approval_callback(path, old, new):
                    result.warning = "write_declined_by_user"
                    continue
            self.adapter.write(path, new)   # sets/clears loop guard
            result.diffs[str(path)] = self._log_diff(path, old, new)
            result.files[str(path)] = new
            result.wrote = True

        self._last_run = time.time()
        return result


# ---------------------------------------------------------------------------
# Self-tests — payloads copied from live probe shapes (2026-08-31)
# ---------------------------------------------------------------------------

def _selftest() -> int:
    import shutil
    failures = 0

    def check(name, cond):
        nonlocal failures
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures += 1

    print(f"{DISTILL_MARKER} selftest")
    now = datetime.now(timezone.utc)

    live = {"id": "abc", "text": "Jarmo runs the fleet from Helsinki",
            "similarity": 1.0748, "surprise_score": 0.8614,
            "utility_score": 0.8, "source_role": "user",
            "created_at": "2026-08-17T17:31:11Z", "speed": "fast",
            "category": "fact", "access_count": 57,
            "content_class": "payload"}
    m = AstralClient._parse_memory(live)
    check("T1 live payload parses", m.source_role == "user" and m.access_count == 57)

    # T2 (v1.2): consolidated tier outranks fast tier, all else equal
    slow = AstralClient._parse_memory({**live, "id": "s", "speed": "slow"})
    check("T2 slow tier scores above fast", slow.score(now=now) > m.score(now=now))

    # T3 (v1.2): access_count is a real signal
    popular = AstralClient._parse_memory({**live, "id": "p", "access_count": 500})
    quiet = AstralClient._parse_memory({**live, "id": "q", "access_count": 0})
    check("T3 popular outranks quiet", popular.score(now=now) > quiet.score(now=now))

    # T4 hygiene filters (AC-12 findings, 2026-09-01)
    check("T4a noise excluded", not eligible(Memory(id="n", text="x", content_class="noise")))
    check("T4b context category excluded",
          not eligible(Memory(id="c", text="Tämä on testipäivitys", category="context")))
    check("T4c status report excluded",
          not eligible(Memory(id="st", text="Test run status at 9 minutes in: 2 of 87 scenarios done")))
    check("T4d meta-memory excluded",
          not eligible(Memory(id="me", text="Learned preferences stored in memory: manual building")))
    check("T4e over-length skipped",
          not eligible(Memory(id="lo", text="x" * 181)))
    check("T4f dormant now eligible (weighted, not excluded)",
          eligible(Memory(id="d", text="User lives in Oulu, Finland (UTC+3).", speed="dormant")))
    check("T4g artifact stripped",
          clean_text("16/87 done [DATES MENTIONED: 16/87]") == "16/87 done")

    # T5 conflict — [user] beats [assistant] on same topic
    a = Memory(id="1", text="favorite editor preference remains emacs always", source_role="assistant")
    u = Memory(id="2", text="favorite editor preference remains helix always", source_role="user")
    check("T5 [user] wins same-topic", [k.id for k in resolve_conflicts([a, u])] == ["2"])

    # T6 sections: split/join round-trip, pinned byte-preserved
    pinned = "User in Oulu, Finland.\nDinar API: LiteLLM proxy.\n"
    joined = join_sections(pinned, "§ [user] extra fact\n")
    p2, d2 = split_sections(joined)
    check("T6a pinned preserved byte-exact", p2 == pinned)
    check("T6b distilled block recovered", d2.strip() == "§ [user] extra fact")
    check("T6c marker in delimiter (guard layer 2)", content_is_distilled(joined))
    check("T6d first run: whole file is pinned", split_sections(pinned) == (pinned, ""))

    # T7 dedup against pinned
    check("T7 pinned-duplicate skipped",
          not not_in_pinned("User is in Oulu, Finland", pinned)
          and not_in_pinned("Squat PR 200kg, two kids", pinned))

    # T8 full pass: augment, never replace
    tmpdir = Path(tempfile.mkdtemp())
    adapter = HermesAdapter(hermes_home=tmpdir)
    adapter.memory_md.parent.mkdir(parents=True)
    curated_mem = "Collaborators: Tommi (infra), Juha (beta).\nUser in Oulu, Finland (UTC+3).\n"
    curated_user = "CDO of Suocommerce, building Astral Core.\n"
    adapter.memory_md.write_text(curated_mem)
    adapter.user_md.write_text(curated_user)

    class FakeClient:
        def fetch_memories(self, *a, **k):
            return [
                Memory(id="1", text="Bot-jarmo is Tailscale-only; user has root.",
                       source_role="dyad", speed="slow", similarity=0.9,
                       surprise_score=0.7, utility_score=0.8, access_count=40),
                Memory(id="2", text="User in Oulu, Finland, UTC+3 timezone.",
                       source_role="user", speed="slow", similarity=0.9),   # dup of pinned
                Memory(id="3", text="Test run status at 9 minutes in: 2 of 87 done",
                       source_role="assistant", speed="fast", similarity=0.99),
                Memory(id="4", text="Formal physics education; interests thermodynamics.",
                       source_role="user", category="fact", speed="slow",
                       similarity=0.8, access_count=30),
            ]
        def fetch_active_preferences(self):
            return [Preference(id="p", text="Prefers one generated file per reply.")]

    eng = DistillationEngine(FakeClient(), adapter, cache_ttl=0,
                             log_path=tmpdir / "distillation.log")
    res = eng.run(force=True)
    mem = adapter.memory_md.read_text()
    usr = adapter.user_md.read_text()
    check("T8a wrote", res.wrote)
    check("T8b curated MEMORY.md text intact", mem.startswith(curated_mem))
    check("T8c curated USER.md text intact", usr.startswith(curated_user))
    check("T8d durable dyad fact added", "Tailscale-only" in mem)
    check("T8e pinned duplicate not re-added", mem.count("Oulu") == 1)
    check("T8f status churn excluded", "Test run status" not in mem)
    check("T8g identity fact lands in USER.md", "physics" in usr and "IDENTITY" in usr)
    check("T8h preference lands in USER.md", "one generated file" in usr)
    check("T8i within total budgets",
          len(mem) <= DEFAULT_MEMORY_BUDGET and len(usr) <= DEFAULT_USER_BUDGET)

    # T9 second pass is idempotent: re-run replaces only the block
    res2 = eng.run(force=True)
    check("T9 idempotent (no diff on identical inputs)", not res2.wrote)

    # T10 unreachable server keeps files
    class Dead:
        def fetch_memories(self, *a, **k): raise urllib.error.URLError("refused")
        def fetch_active_preferences(self): return []
    before = adapter.memory_md.read_text()
    r3 = DistillationEngine(Dead(), adapter, cache_ttl=0, log_path=tmpdir / "d.log").run(force=True)
    check("T10 unreachable -> intact", not r3.wrote and adapter.memory_md.read_text() == before)

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return failures


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    client = AstralClient()
    engine = DistillationEngine(client, HermesAdapter())
    r = engine.run(force=True)
    print(json.dumps({"wrote": r.wrote, "warning": r.warning,
                      "fetched": r.fetched, "selected": r.selected,
                      "files": list(r.files.keys()), "diffs": r.diffs},
                     indent=2))
