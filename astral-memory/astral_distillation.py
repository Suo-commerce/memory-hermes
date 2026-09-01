#!/usr/bin/env python3
# Generated: 2026-08-31
# Purpose: Distillation engine v1.1.0 per SPEC astral-core-distillation-spec
#          v1.1 — rebased on live-verified endpoints (POST /v1/memory/search
#          results schema, POST /v1/memory/list for preferences) and the real
#          memory-hermes plugin idioms (v2.9.1); adds mandatory feedback-loop
#          guard (DL-1) and corrected ~/.hermes/memories/ paths.
"""
astral_distillation.py — v1.1.0

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
import tempfile
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

__version__ = "1.1.0"
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
TIER_WEIGHTS = {"fast": 1.0, "medium": 0.8, "slow": 0.4, "dormant": 0.1}

DEFAULT_MEMORY_BUDGET = 2200
DEFAULT_USER_BUDGET = 1375
DEFAULT_CACHE_TTL = 300
DEFAULT_TIMEOUT = 5
DEFAULT_HALF_LIFE_DAYS = 30.0

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
        return (self.similarity * self.utility_score * self.surprise_score
                * recency * prov * tier)


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

def eligible(m: Memory) -> bool:
    if m.content_class == "noise":
        return False
    if m.category == "diagnostic":
        return False
    return m.speed in ("fast", "medium")


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
                 user_budget: int = DEFAULT_USER_BUDGET):
        base = hermes_home or (Path.home() / ".hermes")
        if profile and profile != "default":
            base = base / "profiles" / profile
        mem_dir = base / "memories"
        self.memory_md = mem_dir / "MEMORY.md"
        self.user_md = mem_dir / "USER.md"
        self._budgets = {self.memory_md: memory_budget, self.user_md: user_budget}

    def get_context_files(self) -> list:
        return [self.memory_md, self.user_md]

    def get_char_budget(self, file_path: Path) -> int:
        return self._budgets[file_path]

    @staticmethod
    def _fmt_entry(m: Memory) -> str:
        return f"\u00a7 [{m.source_role}] {m.text}\n"

    def entry_len(self, m: Memory) -> int:
        return len(self._fmt_entry(m))

    def format_memories(self, memories: list, file_path: Path) -> str:
        header = f"# MEMORY \u2014 distilled from Astral Core {DISTILL_MARKER}\n"
        return header + "".join(self._fmt_entry(m) for m in memories)

    def format_preferences(self, prefs: list, file_path: Path) -> str:
        lines = [f"# USER \u2014 distilled {DISTILL_MARKER}",
                 "[PREFERENCES \u2014 learned, consented]"]
        lines += [f"- {p.text}" for p in prefs if p.status == "active"]
        lines.append("[/PREFERENCES]")
        return "\n".join(lines) + "\n"


class GenericMarkdownAdapter(DistillationAdapter):
    """Single-file agents: CLAUDE.md (Claude Code), AGENTS.md (Codex)."""

    def __init__(self, target: Path, budget: int = DEFAULT_MEMORY_BUDGET):
        self.target = target
        self.budget = budget

    def get_context_files(self) -> list:
        return [self.target]

    def get_char_budget(self, file_path: Path) -> int:
        return self.budget

    def entry_len(self, m: Memory) -> int:
        return len(f"- [{m.source_role}] {m.text}\n")

    def format_memories(self, memories: list, file_path: Path) -> str:
        out = [f"## Astral Core Memory (distilled) {DISTILL_MARKER}\n"]
        out += [f"- [{m.source_role}] {m.text}\n" for m in memories]
        return "".join(out)

    def format_preferences(self, prefs: list, file_path: Path) -> str:
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

        keep = resolve_conflicts([m for m in memories if eligible(m)])
        mem_file, user_file = self.adapter.get_context_files()
        header_len = len(self.adapter.format_memories([], mem_file))
        selected = select_top_k(
            keep, self.adapter.get_char_budget(mem_file) - header_len,
            self.adapter.entry_len)
        selected.sort(key=lambda m: m.score(), reverse=True)

        memory_content = self.adapter.format_memories(selected, mem_file)
        user_content = self.adapter.format_preferences(preferences, user_file)
        budget_u = self.adapter.get_char_budget(user_file)
        if len(user_content) > budget_u:
            lines = user_content.splitlines()
            while len("\n".join(lines)) + 1 > budget_u and len(lines) > 3:
                lines.pop(-2)   # drop last pref, keep closing tag
            user_content = "\n".join(lines) + "\n"

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

    # T1: parse verified live search-result shape
    live = {"id": "abc", "text": "Jarmo runs the fleet from Helsinki",
            "similarity": 1.0748, "surprise_score": 0.8614,
            "utility_score": 0.8, "source_role": "user",
            "created_at": "2026-08-17T17:31:11Z", "speed": "fast",
            "category": "fact", "access_count": 57,
            "content_class": "payload"}
    m = AstralClient._parse_memory(live)
    check("T1 live payload parses", m.source_role == "user"
          and m.access_count == 57 and 0 < m.score(now=now) < 1.1)

    # T2: recency decay monotonic
    old = AstralClient._parse_memory({**live, "id": "old",
                                      "created_at": "2026-02-01T00:00:00Z"})
    check("T2 older memory scores lower", old.score(now=now) < m.score(now=now))

    # T3: eligibility filters
    check("T3a noise excluded",
          not eligible(Memory(id="n", text="x", content_class="noise")))
    check("T3b diagnostic excluded",
          not eligible(Memory(id="d", text="x", category="diagnostic")))
    check("T3c dormant excluded",
          not eligible(Memory(id="s", text="x", speed="dormant")))
    check("T3d fast payload eligible", eligible(m))

    # T4: conflict — same-topic [user] beats [assistant] (AC-4, OQ-6 heuristic)
    a = Memory(id="1", text="favorite editor preference remains emacs always",
               source_role="assistant", similarity=0.9,
               surprise_score=0.9, utility_score=0.9)
    u = Memory(id="2", text="favorite editor preference remains helix always",
               source_role="user", similarity=0.5,
               surprise_score=0.5, utility_score=0.5)
    kept = resolve_conflicts([a, u])
    check("T4 [user] wins same-topic despite lower score",
          len(kept) == 1 and kept[0].id == "2")

    # T5: unrelated memories both kept
    b = Memory(id="3", text="fortress deploy ritual requires md5 verification",
               source_role="assistant")
    kept2 = resolve_conflicts([u, b])
    check("T5 different topics both kept", len(kept2) == 2)

    # T6: multi-query dedup keeps max similarity
    class Fake2Q:
        calls = 0
        def _post(self, path, payload):
            Fake2Q.calls += 1
            sim = 0.3 if Fake2Q.calls == 1 else 0.9
            return {"results": [{**live, "similarity": sim}]}
    c = AstralClient.__new__(AstralClient)
    c._post = Fake2Q()._post
    ms = AstralClient.fetch_memories(c, queries=["q1", "q2"])
    check("T6 dedup keeps max similarity",
          len(ms) == 1 and ms[0].similarity == 0.9)

    # T7: preferences — only metadata.status == "active" (AC-5)
    class FakePrefs:
        def _post(self, path, payload):
            return {"memories": [
                {"id": "p1", "text": "one file per reply",
                 "metadata": {"status": "active"}},
                {"id": "p2", "text": "pending thing",
                 "metadata": {"status": "pending"}},
                {"id": "p3", "text": "declined thing",
                 "metadata": {"status": "declined"}}]}
    c2 = AstralClient.__new__(AstralClient)
    c2._post = FakePrefs()._post
    prefs = AstralClient.fetch_active_preferences(c2)
    check("T7 only active prefs pass", len(prefs) == 1
          and prefs[0].text == "one file per reply")

    # T8: full pass with loop-guard observation
    tmpdir = Path(tempfile.mkdtemp())
    adapter = HermesAdapter(hermes_home=tmpdir, memory_budget=400,
                            user_budget=200)
    guard_seen = {"during": False}
    orig_replace = os.replace
    def spy_replace(src, dst):
        guard_seen["during"] = guard_seen["during"] or _GUARD.is_set()
        return orig_replace(src, dst)
    os.replace = spy_replace
    class FakeClient:
        def fetch_memories(self, *a, **k):
            return [m, b, Memory(id="z", text="noise entry",
                                 content_class="noise")]
        def fetch_active_preferences(self):
            return [Preference(id="p", text="one file per reply")]
    try:
        eng = DistillationEngine(FakeClient(), adapter, cache_ttl=0,
                                 log_path=tmpdir / "distillation.log")
        res = eng.run(force=True)
    finally:
        os.replace = orig_replace
    check("T8a files written under memories/",
          res.wrote and adapter.memory_md.exists()
          and "memories" in str(adapter.memory_md))
    check("T8b loop guard set during write, clear after",
          guard_seen["during"] and not distillation_in_progress())
    check("T8c marker present (guard layer 2)",
          content_is_distilled(adapter.memory_md.read_text()))
    check("T8d noise absent", "noise entry" not in adapter.memory_md.read_text())
    check("T8e diff log written", (tmpdir / "distillation.log").exists())

    # T9: unreachable server keeps existing files (AC-3)
    class DeadClient:
        def fetch_memories(self, *a, **k):
            raise urllib.error.URLError("refused")
        def fetch_active_preferences(self):
            return []
    before = adapter.memory_md.read_text()
    res2 = DistillationEngine(DeadClient(), adapter, cache_ttl=0,
                              log_path=tmpdir / "d.log").run(force=True)
    check("T9 unreachable -> old files intact",
          not res2.wrote and adapter.memory_md.read_text() == before)

    # T10: cache TTL
    eng.cache_ttl = 9999
    check("T10 cache skip", eng.run().warning == "cache_fresh_skip")

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
