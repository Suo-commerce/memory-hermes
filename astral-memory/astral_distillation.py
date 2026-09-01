#!/usr/bin/env python3
# Generated: 2026-09-01
# Purpose: astral_distillation.py v2.0.0 — thin renderer per
#          SPEC-DREAMER-DIGEST-001 v0.2 §3.9. The server's Dreamer digest
#          (grounded, consent-gated) is now the source of the distilled
#          blocks; this module only fetches GET /v1/memory/digest and writes
#          the delimited blocks into MEMORY.md / USER.md. Removed from
#          v1.2.x: coverage queries, scoring, tier weights, recency, dedup,
#          conflict resolution — the server did the thinking. Retained:
#          pinned/distilled sections, config.yaml budgets, atomic writes,
#          DL-1 loop guard + marker, secrets belt-and-braces, --dry-run,
#          --pending preview, diff log.
"""
astral_distillation.py — v2.0.0 (digest renderer)

Data source: GET /v1/memory/digest  -> {"memory": [claim], "user": [claim],
             "generation", "run_id", "pending_count"}
             claim = {id, text, sources, kind, section, status}
Only status == "active" claims are rendered (the endpoint already filters).

Usage:
  python3 astral_distillation.py               # render active digest to files
  python3 astral_distillation.py --dry-run     # show blocks, write nothing
  python3 astral_distillation.py --pending     # preview PENDING digest (never written)
  python3 astral_distillation.py --selftest
"""

from __future__ import annotations

import json
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
from typing import Optional

__version__ = "2.0.0"
DISTILL_MARKER = f"[astral-distillation v{__version__}]"

# ---------------------------------------------------------------------------
# DL-1 loop guard — same contract as v1.x; the plugin's on_memory_write
# imports distillation_in_progress() / relies on the marker literal.
# ---------------------------------------------------------------------------

_GUARD = threading.Event()


def distillation_in_progress() -> bool:
    return _GUARD.is_set()


def content_is_distilled(content: str) -> bool:
    return "[astral-distillation v" in (content or "")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MEMORY_BUDGET = 2200
DEFAULT_USER_BUDGET = 1375
DEFAULT_CACHE_TTL = 300
DEFAULT_TIMEOUT = int(os.environ.get("ASTRAL_DISTILL_TIMEOUT", "30"))

DISTILLED_BEGIN = "<!-- astral:distilled:begin {marker} — auto-generated, do not edit -->"
DISTILLED_END = "<!-- astral:distilled:end -->"
_BEGIN_RE = re.compile(r"<!-- astral:distilled:begin .*? -->\n?", re.S)
_END_RE = re.compile(r"<!-- astral:distilled:end -->\n?")

# Belt-and-braces secrets filter (server scrub is authoritative; this is the
# last line before text enters a system prompt). Same family as v1.2.6.
_SECRET_RE = re.compile(
    r"(password|passwd|passphrase|secret|api[_ -]?key|api[_ -]?token|bearer|"
    r"private[_ -]?key|credential|BEGIN (RSA|EC|OPENSSH) PRIVATE|"
    r"sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|xox[bp]-|AKIA[0-9A-Z]{12,}|"
    r"[A-Fa-f0-9]{40,}|"
    r"[\w.+-]+@[\w-]+\.[\w.]+.{0,40}(password|passwd|pw\b)|"
    r"(password|passwd|pw)\b.{0,40}[\w.+-]+@[\w-]+\.[\w.]+)", re.I | re.S)

# USER.md section routing by claim kind (§3.9)
_PREFERENCE_KINDS = {"preference"}
_IDENTITY_KINDS = {"identity", "style", "other"}


# ---------------------------------------------------------------------------
# Hermes config budgets (unchanged from v1.2.3)
# ---------------------------------------------------------------------------

_CFG_MEM_RE = re.compile(r"^\s*memory_char_limit\s*:\s*(\d+)", re.M)
_CFG_USER_RE = re.compile(r"^\s*user_char_limit\s*:\s*(\d+)", re.M)


def hermes_budgets(hermes_home: Optional[Path] = None) -> tuple:
    cfg = (hermes_home or Path.home() / ".hermes") / "config.yaml"
    mem, usr = DEFAULT_MEMORY_BUDGET, DEFAULT_USER_BUDGET
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return mem, usr
    m1, m2 = _CFG_MEM_RE.search(text), _CFG_USER_RE.search(text)
    if m1:
        mem = int(m1.group(1))
    if m2:
        usr = int(m2.group(1))
    return mem, usr


# ---------------------------------------------------------------------------
# Sections (unchanged from v1.2.5): pinned is byte-preserved
# ---------------------------------------------------------------------------

def split_sections(content: str) -> tuple:
    b = _BEGIN_RE.search(content or "")
    if not b:
        return (content or ""), ""
    e = _END_RE.search(content, b.end())
    head = content[:b.start()]
    if head.endswith("\u00a7\n"):
        head = head[:-2]
    if not e:
        return head, content[b.end():]
    return head + content[e.end():], content[b.end():e.start()]


def join_sections(pinned: str, distilled: str, native_sep: bool = False) -> str:
    if not distilled.strip():
        return pinned
    sep = "" if (not pinned or pinned.endswith("\n")) else "\n"
    lead = "\u00a7\n" if (native_sep and pinned.strip()
                        and not pinned.rstrip().endswith("\u00a7")) else ""
    return (pinned + sep + lead
            + DISTILLED_BEGIN.format(marker=DISTILL_MARKER) + "\n"
            + distilled.rstrip("\n") + "\n"
            + DISTILLED_END + "\n")


# ---------------------------------------------------------------------------
# Digest client
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    id: str
    text: str
    kind: str = "other"
    section: str = "memory"
    sources: list = field(default_factory=list)


@dataclass
class Digest:
    memory: list = field(default_factory=list)
    user: list = field(default_factory=list)
    generation: int = 0
    pending_count: int = 0


class DigestClient:
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

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(self.base_url + path)
        if self.api_token:
            req.add_header("Authorization", f"Bearer {self.api_token}")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _parse(claims: list, default_section: str) -> list:
        out = []
        for c in claims or []:
            out.append(Claim(id=str(c.get("id", "")),
                             text=c.get("text", ""),
                             kind=str(c.get("kind", "other")).lower(),
                             section=str(c.get("section", default_section)).lower(),
                             sources=list(c.get("sources", []) or [])))
        return out

    def fetch_digest(self) -> Digest:
        d = self._get("/v1/memory/digest")
        return Digest(memory=self._parse(d.get("memory"), "memory"),
                      user=self._parse(d.get("user"), "user"),
                      generation=int(d.get("generation", 0)),
                      pending_count=int(d.get("pending_count", 0)))

    def fetch_pending(self) -> Digest:
        """--pending preview: same shapes from /digest/pending (flat list)."""
        rows = self._get("/v1/memory/digest/pending")
        dg = Digest()
        for c in self._parse(rows, "memory"):
            (dg.user if c.section == "user" else dg.memory).append(c)
        return dg


# ---------------------------------------------------------------------------
# Rendering (§3.9)
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join((text or "").split())


def safe_claims(claims: list) -> list:
    """Belt-and-braces: drop any claim that looks credential-shaped."""
    return [c for c in claims if c.text.strip() and not _SECRET_RE.search(c.text)]


def render_memory_block(claims: list) -> str:
    """Native Hermes entries: standalone § separators, [digest] tag inline."""
    entries = [f"[digest] {_clean(c.text)}\n" for c in claims]
    return "\u00a7\n".join(entries)


def render_user_block(claims: list) -> str:
    prefs = [c for c in claims if c.kind in _PREFERENCE_KINDS]
    ident = [c for c in claims if c.kind in _IDENTITY_KINDS]
    out = []
    if prefs:
        out.append("[PREFERENCES \u2014 learned, consented]")
        out += [f"- {_clean(c.text)}" for c in prefs]
        out.append("[/PREFERENCES]")
    if ident:
        out.append("[IDENTITY \u2014 stated by user]")
        out += [f"- {_clean(c.text)}" for c in ident]
        out.append("[/IDENTITY]")
    return ("\n".join(out) + "\n") if out else ""


def fit_block(claims: list, render, room: int) -> str:
    """Drop trailing claims until the rendered block fits. The server already
    trimmed to budget; this only handles a smaller local room (large pinned)."""
    cs = list(claims)
    while cs:
        block = render(cs)
        if len(block) <= room:
            return block
        cs.pop()
    return ""


# ---------------------------------------------------------------------------
# Adapter (paths + atomic write; unchanged behavior)
# ---------------------------------------------------------------------------

class HermesAdapter:
    def __init__(self, hermes_home: Optional[Path] = None,
                 profile: Optional[str] = None,
                 memory_budget: Optional[int] = None,
                 user_budget: Optional[int] = None):
        base = hermes_home or (Path.home() / ".hermes")
        cfg_mem, cfg_usr = hermes_budgets(base)
        self.memory_budget = memory_budget if memory_budget is not None else cfg_mem
        self.user_budget = user_budget if user_budget is not None else cfg_usr
        if profile and profile != "default":
            base = base / "profiles" / profile
        mem_dir = base / "memories"
        self.memory_md = mem_dir / "MEMORY.md"
        self.user_md = mem_dir / "USER.md"

    def write(self, file_path: Path, content: str) -> None:
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


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class RenderResult:
    wrote: bool
    generation: int = 0
    pending_count: int = 0
    files: dict = field(default_factory=dict)
    diffs: dict = field(default_factory=dict)
    warning: Optional[str] = None


class DistillationEngine:
    def __init__(self, client: DigestClient, adapter: HermesAdapter,
                 cache_ttl: int = DEFAULT_CACHE_TTL,
                 log_path: Optional[Path] = None,
                 dry_run: bool = False):
        self.client = client
        self.adapter = adapter
        self.cache_ttl = cache_ttl
        self.log_path = log_path or (Path.home() / ".hermes" / "logs"
                                     / "distillation.log")
        self.dry_run = dry_run
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

    def run(self, force: bool = False, pending_preview: bool = False) -> RenderResult:
        if not force and (time.time() - self._last_run) < self.cache_ttl:
            return RenderResult(wrote=False, warning="cache_fresh_skip")

        try:
            digest = (self.client.fetch_pending() if pending_preview
                      else self.client.fetch_digest())
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                json.JSONDecodeError, TimeoutError) as e:
            return RenderResult(
                wrote=False, warning=f"astral_unreachable_keep_existing: {e}")

        mem_claims = safe_claims(digest.memory)
        user_claims = safe_claims(digest.user)

        a = self.adapter
        old_mem = a.memory_md.read_text(encoding="utf-8") if a.memory_md.exists() else ""
        old_user = a.user_md.read_text(encoding="utf-8") if a.user_md.exists() else ""
        pinned_mem, _ = split_sections(old_mem)
        pinned_user, _ = split_sections(old_user)

        overhead = len(DISTILLED_BEGIN.format(marker=DISTILL_MARKER)) + len(DISTILLED_END) + 4
        mem_room = a.memory_budget - len(pinned_mem) - overhead
        user_room = a.user_budget - len(pinned_user) - overhead

        mem_block = fit_block(mem_claims, render_memory_block, max(0, mem_room))
        user_block = fit_block(user_claims, render_user_block, max(0, user_room))

        result = RenderResult(wrote=False, generation=digest.generation,
                              pending_count=digest.pending_count)

        pairs = ((a.memory_md, join_sections(pinned_mem, mem_block, native_sep=True)),
                 (a.user_md, join_sections(pinned_user, user_block)))
        for path, new in pairs:
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            if old == new:
                continue
            _, block = split_sections(new)
            if self.dry_run or pending_preview:
                result.files[str(path)] = block
                result.warning = "pending_preview" if pending_preview else "dry_run"
                continue
            a.write(path, new)
            result.diffs[str(path)] = self._log_diff(path, old, new)
            result.files[str(path)] = block
            result.wrote = True

        self._last_run = time.time()
        return result


# ---------------------------------------------------------------------------
# Self-tests
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

    # T1: parse the real /v1/memory/digest response shape (2026-09-01 run)
    body = {"memory": [
                {"id": "d2f6e4a50679", "text": "Storm Gate is the egress chokepoint for model I/O.",
                 "sources": ["689f605e3130"], "kind": "environment",
                 "section": "memory", "status": "active"}],
            "user": [
                {"id": "a132e994c951", "text": "User prefers one generated file per reply.",
                 "sources": ["6de1292b5116"], "kind": "preference",
                 "section": "user", "status": "active"},
                {"id": "c95b3bca0d17", "text": "User communicates terse, declarative instructions.",
                 "sources": ["x"], "kind": "style", "section": "user",
                 "status": "active"}],
            "generation": 1, "run_id": "digest_x", "pending_count": 0}
    c = DigestClient.__new__(DigestClient)
    c._get = lambda p: body
    dg = DigestClient.fetch_digest(c)
    check("T1 digest parses", len(dg.memory) == 1 and len(dg.user) == 2
          and dg.generation == 1)

    # T2: kind routing — preference vs style/identity
    ub = render_user_block(dg.user)
    check("T2a preference under [PREFERENCES]",
          "[PREFERENCES" in ub and "one generated file" in ub)
    check("T2b style under [IDENTITY]",
          "[IDENTITY" in ub and "terse, declarative" in ub)

    # T3: memory block native format
    mb = render_memory_block(dg.memory)
    check("T3 native format", mb.startswith("[digest] ") and "\u00a7 [" not in mb)

    # T4: belt-and-braces secrets filter
    bad = Claim(id="s", text="Staging password Tokqo1 for user@example.com")
    check("T4 credential claim dropped", safe_claims([bad, dg.memory[0]]) == [dg.memory[0]])

    # T5: full render — pinned preserved, block delimited, budgets held
    tmp = Path(tempfile.mkdtemp())
    adapter = HermesAdapter(hermes_home=tmp, memory_budget=800, user_budget=500)
    adapter.memory_md.parent.mkdir(parents=True)
    curated = "Collaborators: Tommi (infra).\n\u00a7\nAlways use Astral memory.\n"
    adapter.memory_md.write_text(curated)
    adapter.user_md.write_text("CDO of Suocommerce.\n")
    eng = DistillationEngine(c if False else DigestClient.__new__(DigestClient),
                             adapter, cache_ttl=0, log_path=tmp / "d.log")
    eng.client._get = lambda p: body
    r = eng.run(force=True)
    mem = adapter.memory_md.read_text()
    usr = adapter.user_md.read_text()
    check("T5a wrote", r.wrote)
    check("T5b pinned byte-intact", mem.startswith(curated))
    check("T5c delimited + marker", content_is_distilled(mem)
          and mem.count(DISTILLED_END) == 1)
    check("T5d lead § before block", "Always use Astral memory.\n\u00a7\n<!--" in mem)
    check("T5e budgets held", len(mem) <= 800 and len(usr) <= 500)
    check("T5f DL-1 guard cleared", not distillation_in_progress())

    # T6: idempotent re-run
    r2 = eng.run(force=True)
    check("T6 idempotent", not r2.wrote)

    # T7: replaces a v1.2.4 block cleanly
    adapter.memory_md.write_text(
        curated + "<!-- astral:distilled:begin [astral-distillation v1.2.4] \u2014 auto-generated, do not edit -->\n"
        "\u00a7 [user] old entry\n<!-- astral:distilled:end -->\n")
    r3 = eng.run(force=True)
    m3 = adapter.memory_md.read_text()
    check("T7 v1.2.4 block replaced", "old entry" not in m3
          and "v2.0.0" in m3 and m3.startswith(curated))

    # T8: unreachable server keeps files
    class Dead:
        api_token = ""
        def fetch_digest(self):
            raise urllib.error.URLError("refused")
        def fetch_pending(self):
            raise urllib.error.URLError("refused")
    before = adapter.memory_md.read_text()
    r4 = DistillationEngine(Dead(), adapter, cache_ttl=0, log_path=tmp / "d2.log").run(force=True)
    check("T8 unreachable -> intact", not r4.wrote
          and adapter.memory_md.read_text() == before)

    # T9: dry-run
    r5 = DistillationEngine(eng.client, adapter, cache_ttl=0,
                            log_path=tmp / "d3.log", dry_run=True).run(force=True)
    check("T9 dry-run no writes", not r5.wrote and r5.warning in ("dry_run", None))

    # T10: empty digest -> block removed, pinned only
    eng.client._get = lambda p: {"memory": [], "user": [], "generation": 2,
                                 "run_id": "", "pending_count": 0}
    r6 = eng.run(force=True)
    check("T10 empty digest -> pinned only",
          adapter.memory_md.read_text() == curated)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{'ALL PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return failures


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    pending = "--pending" in sys.argv
    dry = "--dry-run" in sys.argv or pending
    adapter = HermesAdapter()
    engine = DistillationEngine(DigestClient(), adapter, dry_run=dry)
    r = engine.run(force=True, pending_preview=pending)
    for path, block in r.files.items():
        label = "PENDING preview" if pending else ("would write" if dry else "wrote")
        print(f"\n--- {label}: {Path(path).name} ---\n{block}")
    print(json.dumps({"wrote": r.wrote, "warning": r.warning,
                      "generation": r.generation,
                      "pending_count": r.pending_count,
                      "budgets": {"memory": adapter.memory_budget,
                                  "user": adapter.user_budget},
                      "files": list(r.files.keys()),
                      "diffs": r.diffs}, indent=2))
