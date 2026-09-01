#!/usr/bin/env python3
# Generated: 2026-08-31
# Purpose: Core distillation engine per SPEC astral-core-distillation-spec v1.0 —
#          derives agent context files (MEMORY.md/USER.md/CLAUDE.md/AGENTS.md) as
#          computed views of Astral Core Memory (:8090), with provenance-weighted
#          scoring, [user]-wins conflict resolution, atomic writes, and diff audit.
"""
astral_distillation.py — Distillation Adapter layer for Astral Core Memory.

Astral Core is the source of truth (Locked Assumption #1). This module:
  1. Fetches GET /v1/memory/briefing and GET /v1/preferences/active
  2. Scores entries: relevance x recency x importance x provenance_weight
  3. Resolves conflicts ([user] > [dyad] > [assistant], per SPEC §8)
  4. Selects top-K within per-file char budgets
  5. Formats per-agent (Hermes / Claude Code / Codex / custom)
  6. Writes atomically, logs diff to distillation.log

The Astral Core server is never modified (Locked Assumption #8, Non-Goals).
Run `python3 astral_distillation.py --selftest` for the built-in test suite.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

__version__ = "1.0.0"
DISTILL_MARKER = "[astral-distillation v1.0.0]"  # log-line literal for boot grep

# ---------------------------------------------------------------------------
# Constants (SPEC §2 Locked Assumption #4, §4.2)
# ---------------------------------------------------------------------------

PROVENANCE_WEIGHTS = {
    "user": 1.0,
    "dyad": 0.8,
    "assistant": 0.5,
    "diagnostic": 0.1,
}

# Conflict resolution precedence (SPEC §8): higher rank wins on same topic.
# [user] beats everything; [dyad] beats [assistant] (closer to conversation).
PROVENANCE_RANK = {"user": 3, "dyad": 2, "assistant": 1, "diagnostic": 0}

DEFAULT_MEMORY_BUDGET = 2200   # chars, MEMORY.md
DEFAULT_USER_BUDGET = 1375     # chars, USER.md
DEFAULT_CACHE_TTL = 300        # seconds
DEFAULT_TIMEOUT = 5            # seconds per REST call (AC-7: total <500ms typical)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Memory:
    id: str
    text: str
    provenance: str = "assistant"          # user | dyad | assistant | diagnostic
    relevance: float = 1.0
    recency: float = 1.0
    importance: float = 1.0
    tier: str = "fast"                      # fast | medium | slow | dormant
    topic: Optional[str] = None             # for conflict resolution grouping
    session_specific: bool = False

    @property
    def score(self) -> float:
        w = PROVENANCE_WEIGHTS.get(self.provenance, 0.5)
        return self.relevance * self.recency * self.importance * w


@dataclass
class Preference:
    id: str
    text: str
    status: str = "approved"                # approved | pending | declined | withdrawn


@dataclass
class DistillationResult:
    wrote: bool
    files: dict[str, str] = field(default_factory=dict)   # path -> new content
    diffs: dict[str, dict] = field(default_factory=dict)  # path -> {added, removed}
    warning: Optional[str] = None


# ---------------------------------------------------------------------------
# Astral Core client (read-only; server untouched — SPEC §4.1)
# ---------------------------------------------------------------------------

class AstralClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8090",
                 api_token: Optional[str] = None,
                 token_path: Optional[Path] = None,
                 timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_token = api_token or self._load_token(token_path)

    @staticmethod
    def _load_token(token_path: Optional[Path]) -> Optional[str]:
        # SPEC §14: token at ~/.astralcore/api_token; Hermes plugin config
        # (~/.hermes/astral-memory.json, "api_token" field) is the fallback.
        candidates = [token_path] if token_path else [
            Path.home() / ".astralcore" / "api_token",
            Path.home() / ".hermes" / "astral-memory.json",
        ]
        for p in candidates:
            if p and p.exists():
                raw = p.read_text(encoding="utf-8").strip()
                if p.suffix == ".json":
                    try:
                        return json.loads(raw).get("api_token")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                return raw
        return None

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(self.base_url + path)
        if self.api_token:
            req.add_header("Authorization", f"Bearer {self.api_token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_briefing(self) -> list[Memory]:
        data = self._get("/v1/memory/briefing")
        return [self._parse_memory(m) for m in data.get("memories", [])]

    def fetch_active_preferences(self) -> list[Preference]:
        data = self._get("/v1/preferences/active")
        return [
            Preference(id=str(p.get("id", "")), text=p.get("text", ""),
                       status=p.get("status", "approved"))
            for p in data.get("preferences", [])
        ]

    @staticmethod
    def _parse_memory(m: dict) -> Memory:
        return Memory(
            id=str(m.get("id", "")),
            text=m.get("text", m.get("content", "")),
            provenance=m.get("provenance", "assistant").strip("[]").lower(),
            relevance=float(m.get("relevance", 1.0)),
            recency=float(m.get("recency", 1.0)),
            importance=float(m.get("importance", 1.0)),
            tier=m.get("tier", "fast").lower(),
            topic=m.get("topic"),
            session_specific=bool(m.get("session_specific", False)),
        )


# ---------------------------------------------------------------------------
# Scoring / selection / conflict resolution (SPEC §4.2, §8)
# ---------------------------------------------------------------------------

def resolve_conflicts(memories: list[Memory]) -> list[Memory]:
    """Same-topic conflict: keep highest provenance rank (SPEC §8).
    Memories without a topic are never treated as conflicting."""
    by_topic: dict[str, Memory] = {}
    untopiced: list[Memory] = []
    for m in memories:
        if not m.topic:
            untopiced.append(m)
            continue
        cur = by_topic.get(m.topic)
        if cur is None:
            by_topic[m.topic] = m
        else:
            a, b = PROVENANCE_RANK.get(cur.provenance, 0), PROVENANCE_RANK.get(m.provenance, 0)
            if b > a or (b == a and m.score > cur.score):
                by_topic[m.topic] = m
    return untopiced + list(by_topic.values())


def eligible_for_memory_md(m: Memory) -> bool:
    """SPEC §4.3 content mapping: exclude [diagnostic] and session-specific."""
    if m.provenance == "diagnostic":
        return False
    if m.session_specific:
        return False
    return m.tier in ("fast", "medium")


def select_top_k(memories: list[Memory], budget: int,
                 fmt_len) -> list[Memory]:
    """Greedy top-K by score that fits within the char budget.
    fmt_len(memory) -> chars this entry will occupy once formatted."""
    chosen, used = [], 0
    for m in sorted(memories, key=lambda x: x.score, reverse=True):
        cost = fmt_len(m)
        if used + cost <= budget:
            chosen.append(m)
            used += cost
    return chosen


# ---------------------------------------------------------------------------
# Adapter interface (SPEC §6.1) + Hermes implementation (SPEC §4.3)
# ---------------------------------------------------------------------------

class DistillationAdapter:
    """Base interface for agent-specific distillation adapters (SPEC §6.1)."""

    def get_context_files(self) -> list[Path]:
        raise NotImplementedError

    def get_char_budget(self, file_path: Path) -> int:
        raise NotImplementedError

    def format_memories(self, memories: list[Memory], file_path: Path) -> str:
        raise NotImplementedError

    def format_preferences(self, preferences: list[Preference], file_path: Path) -> str:
        raise NotImplementedError

    def write(self, file_path: Path, content: str) -> None:
        """Atomic write: tmp file in same dir + os.replace."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(file_path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, file_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


class HermesAdapter(DistillationAdapter):
    """Writes MEMORY.md (memories) and USER.md (preferences + identity)."""

    def __init__(self, hermes_home: Optional[Path] = None,
                 profile: Optional[str] = None,
                 memory_budget: int = DEFAULT_MEMORY_BUDGET,
                 user_budget: int = DEFAULT_USER_BUDGET):
        base = hermes_home or (Path.home() / ".hermes")
        if profile and profile != "default":
            base = base / "profiles" / profile   # profile isolation (SPEC §4.3)
        self.memory_md = base / "MEMORY.md"
        self.user_md = base / "USER.md"
        self._budgets = {self.memory_md: memory_budget, self.user_md: user_budget}

    def get_context_files(self) -> list[Path]:
        return [self.memory_md, self.user_md]

    def get_char_budget(self, file_path: Path) -> int:
        return self._budgets[file_path]

    def entry_len(self, m: Memory) -> int:
        return len(self._fmt_entry(m))

    @staticmethod
    def _fmt_entry(m: Memory) -> str:
        # §-separated entries with inline provenance annotation (SPEC §8)
        return f"§ [{m.provenance}] {m.text}\n"

    def format_memories(self, memories: list[Memory], file_path: Path) -> str:
        header = f"# MEMORY — distilled from Astral Core {DISTILL_MARKER}\n"
        return header + "".join(self._fmt_entry(m) for m in memories)

    def format_preferences(self, preferences: list[Preference], file_path: Path) -> str:
        lines = ["[PREFERENCES — learned, consented]"]
        lines += [f"- {p.text}" for p in preferences if p.status == "approved"]
        lines.append("[/PREFERENCES]")
        return "\n".join(lines) + "\n"


class GenericMarkdownAdapter(DistillationAdapter):
    """Single-file adapters: Claude Code (CLAUDE.md), Codex (AGENTS.md)."""

    def __init__(self, target: Path, budget: int = DEFAULT_MEMORY_BUDGET,
                 section_title: str = "Astral Core Memory (distilled)"):
        self.target = target
        self.budget = budget
        self.section_title = section_title

    def get_context_files(self) -> list[Path]:
        return [self.target]

    def get_char_budget(self, file_path: Path) -> int:
        return self.budget

    def entry_len(self, m: Memory) -> int:
        return len(f"- [{m.provenance}] {m.text}\n")

    def format_memories(self, memories: list[Memory], file_path: Path) -> str:
        out = [f"## {self.section_title}\n"]
        out += [f"- [{m.provenance}] {m.text}\n" for m in memories]
        return "".join(out)

    def format_preferences(self, preferences: list[Preference], file_path: Path) -> str:
        approved = [p for p in preferences if p.status == "approved"]
        if not approved:
            return ""
        out = ["\n### Preferences (user-approved)\n"]
        out += [f"- {p.text}\n" for p in approved]
        return "".join(out)


# ---------------------------------------------------------------------------
# Engine: fetch -> score -> select -> format -> write -> log (SPEC §9)
# ---------------------------------------------------------------------------

class DistillationEngine:
    def __init__(self, client: AstralClient, adapter: HermesAdapter,
                 cache_ttl: int = DEFAULT_CACHE_TTL,
                 log_path: Optional[Path] = None,
                 write_approval: bool = False,
                 approval_callback=None):
        self.client = client
        self.adapter = adapter
        self.cache_ttl = cache_ttl
        self.log_path = log_path or (Path.home() / ".hermes" / "logs" / "distillation.log")
        self.write_approval = write_approval
        self.approval_callback = approval_callback  # (path, old, new) -> bool
        self._last_run: float = 0.0

    # -- cache TTL (SPEC §5) -------------------------------------------------
    def _cache_fresh(self) -> bool:
        return (time.time() - self._last_run) < self.cache_ttl

    # -- diff logging (AC-8) -------------------------------------------------
    @staticmethod
    def _entry_set(content: str) -> set[str]:
        return {ln.strip() for ln in content.splitlines()
                if ln.strip() and not ln.startswith("#")}

    def _log_diff(self, path: Path, old: str, new: str) -> dict:
        old_set, new_set = self._entry_set(old), self._entry_set(new)
        diff = {"added": sorted(new_set - old_set), "removed": sorted(old_set - new_set)}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "marker": DISTILL_MARKER,
                "file": str(path),
                **diff,
            }) + "\n")
        return diff

    # -- main pass -----------------------------------------------------------
    def run(self, force: bool = False) -> DistillationResult:
        if not force and self._cache_fresh():
            return DistillationResult(wrote=False, warning="cache_fresh_skip")

        # Fetch (graceful degradation on failure — SPEC §4.5, AC-3)
        try:
            memories = self.client.fetch_briefing()
            preferences = self.client.fetch_active_preferences()
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
            return DistillationResult(
                wrote=False,
                warning=f"astral_unreachable_keep_existing: {e}")

        # Score + filter + resolve + select
        eligible = [m for m in memories if eligible_for_memory_md(m)]
        resolved = resolve_conflicts(eligible)
        mem_file, user_file = self.adapter.get_context_files()
        header_len = len(self.adapter.format_memories([], mem_file))
        selected = select_top_k(
            resolved,
            self.adapter.get_char_budget(mem_file) - header_len,
            self.adapter.entry_len)
        # Keep score order in output (SPEC §4.3: top entries sorted by score)
        selected.sort(key=lambda m: m.score, reverse=True)

        # Format
        memory_content = self.adapter.format_memories(selected, mem_file)
        approved = [p for p in preferences if p.status == "approved"]  # AC-5
        user_content = self.adapter.format_preferences(approved, user_file)
        if len(user_content) > self.adapter.get_char_budget(user_file):
            # Trim lowest-priority preference lines until it fits
            lines = user_content.splitlines()
            while len("\n".join(lines)) + 1 > self.adapter.get_char_budget(user_file) \
                    and len(lines) > 2:
                lines.pop(-2)  # remove last preference, keep closing tag
            user_content = "\n".join(lines) + "\n"

        result = DistillationResult(wrote=False)
        for path, new in ((mem_file, memory_content), (user_file, user_content)):
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            if old == new:
                continue
            if self.write_approval and self.approval_callback:  # AC-9
                if not self.approval_callback(path, old, new):
                    result.warning = "write_declined_by_user"
                    continue
            self.adapter.write(path, new)
            result.diffs[str(path)] = self._log_diff(path, old, new)
            result.files[str(path)] = new
            result.wrote = True

        self._last_run = time.time()
        return result


# ---------------------------------------------------------------------------
# Self-tests (run: python3 astral_distillation.py --selftest)
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

    # T1: provenance scoring order
    ms = [Memory(id=str(i), text="x", provenance=p)
          for i, p in enumerate(["diagnostic", "assistant", "dyad", "user"])]
    scores = [m.score for m in ms]
    check("T1 provenance weight ordering", scores == sorted(scores))

    # T2: conflict resolution — [user] beats [assistant] (AC-4)
    a = Memory(id="1", text="old belief", provenance="assistant", topic="editor")
    u = Memory(id="2", text="user truth", provenance="user", topic="editor")
    kept = resolve_conflicts([a, u])
    check("T2 [user] wins same-topic conflict",
          len(kept) == 1 and kept[0].id == "2")

    # T3: [dyad] beats [assistant]
    d = Memory(id="3", text="dyad", provenance="dyad", topic="t")
    a2 = Memory(id="4", text="asst", provenance="assistant", topic="t")
    kept = resolve_conflicts([a2, d])
    check("T3 [dyad] beats [assistant]", kept[0].id == "3")

    # T4: eligibility — diagnostic and session-specific excluded
    check("T4a diagnostic excluded",
          not eligible_for_memory_md(Memory(id="5", text="x", provenance="diagnostic")))
    check("T4b session-specific excluded",
          not eligible_for_memory_md(Memory(id="6", text="x", session_specific=True)))
    check("T4c dormant tier excluded",
          not eligible_for_memory_md(Memory(id="7", text="x", tier="dormant")))

    # T5: budget respected (AC-1)
    tmpdir = Path(tempfile.mkdtemp())
    adapter = HermesAdapter(hermes_home=tmpdir, memory_budget=200, user_budget=150)
    big = [Memory(id=str(i), text="m" * 40, importance=1.0 - i * 0.01)
           for i in range(20)]
    sel = select_top_k(big, 200, adapter.entry_len)
    content = adapter.format_memories(sel, adapter.memory_md)
    check("T5 MEMORY.md within budget", len(content) <= 200 + 80)  # + header

    # T6: pending preferences never distilled (AC-5)
    prefs = [Preference(id="p1", text="approved one", status="approved"),
             Preference(id="p2", text="sneaky pending", status="pending")]
    out = adapter.format_preferences(prefs, adapter.user_md)
    check("T6 pending pref excluded",
          "approved one" in out and "sneaky pending" not in out)

    # T7: atomic write + diff log
    class FakeClient:
        def fetch_briefing(self):
            return [Memory(id="1", text="fact A", provenance="user"),
                    Memory(id="2", text="note", provenance="diagnostic")]
        def fetch_active_preferences(self):
            return [Preference(id="p", text="one file per reply", status="approved")]
    eng = DistillationEngine(FakeClient(), adapter,
                             log_path=tmpdir / "distillation.log", cache_ttl=0)
    res = eng.run(force=True)
    check("T7a files written", res.wrote and adapter.memory_md.exists())
    check("T7b diagnostic absent from output",
          "note" not in adapter.memory_md.read_text())
    check("T7c diff log written", (tmpdir / "distillation.log").exists())

    # T8: graceful degradation (AC-3) — unreachable server keeps old files
    class DeadClient:
        def fetch_briefing(self):
            raise urllib.error.URLError("connection refused")
        def fetch_active_preferences(self):
            raise urllib.error.URLError("connection refused")
    before = adapter.memory_md.read_text()
    eng2 = DistillationEngine(DeadClient(), adapter,
                              log_path=tmpdir / "distillation.log", cache_ttl=0)
    res2 = eng2.run(force=True)
    check("T8 unreachable -> no write, old file intact",
          not res2.wrote and adapter.memory_md.read_text() == before
          and res2.warning and "unreachable" in res2.warning)

    # T9: cache TTL skip
    eng.cache_ttl = 9999
    res3 = eng.run()
    check("T9 cache TTL skips re-run", not res3.wrote and res3.warning == "cache_fresh_skip")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return failures


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    # Manual mode (SPEC §10 Phase 1): explicit one-shot distillation
    client = AstralClient()
    engine = DistillationEngine(client, HermesAdapter())
    r = engine.run(force=True)
    print(json.dumps({"wrote": r.wrote, "warning": r.warning,
                      "files": list(r.files.keys()),
                      "diffs": r.diffs}, indent=2))
