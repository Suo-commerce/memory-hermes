#!/usr/bin/env python3
# Generation Timestamp: 2026-08-18T22:10:00Z
# Purpose: RT-13 Layer A F2 (Amendment A5 §2/§8) — wire fidelity.py into the
#          Hermes provider __init__.py (v2.8.0 → v2.9.0) and bump plugin.yaml.
#          Discipline: backup → exact-anchor preflight (count == 1 each) →
#          replace → post-verify → idempotency guard. Never partial-applies.
#
# Usage (On samwain, in the memory-hermes repo):
#   python3 apply_f2_fidelity_hook.py astral-memory
#   python3 astral-memory/fidelity.py            # self-test still passes
#   python3 -c "import ast,sys;ast.parse(open('astral-memory/__init__.py').read())"
#
# What F2 adds to __init__.py:
#   1. `from . import fidelity as _fidelity` (soft import; provider works if absent)
#   2. state: _served_texts[sid] = {id: text}, _fidelity_pending[sid] = {...}
#   3. config: "fidelity" (default true) — plugin-side gate; server observation
#      mode is the real safety (A5 §3.2)
#   4. _tool_recall: capture id→text from results into _served_texts
#   5. prefetch: capture id→text if server returns memories/results with text
#   6. sync_turn: snapshot served ids for this turn, run A1 vs assistant_content,
#      POST /v1/memories/fidelity (non-blocking, non-fatal), stash pending for A2
#   7. on_turn_start: if pending, run A2(message vs prev_user), POST, clear
#   8. _VERSION 2.9.0 + header changelog; plugin.yaml version 2.9.0
#
# Not touched: schemas.py (no new tool — fidelity is invisible to the LLM).

import re
import shutil
import sys
from pathlib import Path

if len(sys.argv) != 2:
    print(__doc__)
    sys.exit(2)

root = Path(sys.argv[1])
init = root / "__init__.py"
manifest = root / "plugin.yaml"
fid = root / "fidelity.py"

for p in (init, manifest):
    if not p.exists():
        sys.exit(f"missing {p}")
if not fid.exists():
    sys.exit(f"missing {fid} — drop fidelity.py (F1) in first")

src = init.read_text(encoding="utf-8")
man = manifest.read_text(encoding="utf-8")

# ── Idempotency guard ────────────────────────────────────────────────────────
if "_fidelity_pending" in src or 'RT-13' in src:
    sys.exit("__init__.py already contains F2 (RT-13) — refusing to re-apply")

# ── Edits: (anchor, replacement) — every anchor must occur exactly once ──────
EDITS = []

# 1. header purpose + changelog
EDITS.append((
    "# Generation Timestamp: 2026-08-17T09:00:00Z\n"
    "# Purpose: Hermes memory plugin — v2.8.0 renders read-side provenance\n",
    "# Generation Timestamp: 2026-08-18T22:10:00Z\n"
    "# Purpose: Hermes memory plugin — v2.9.0 adds RT-13 Layer A response-\n"
    "#          fidelity feedback (SPEC-REMEMBERING-TOUCH-001 Amendment A5 §2):\n"
    "#          A1 evidence-use on the same turn and A2 next-turn continuation/\n"
    "#          correction, POSTed as id-only signals to /v1/memories/fidelity.\n"
    "#          v2.8.0 rendered read-side provenance\n",
))
EDITS.append((
    "# v2.8.0 CHANGES (P-2 provenance rendering + A3-3 session_scope):\n",
    "# v2.9.0 CHANGES (RT-13 Layer A — Amendment A5 §2, F2):\n"
    "#   NEW  — fidelity.py (F1) soft-imported; two pure classifiers, no LLM.\n"
    "#   NEW  — _served_texts[sid]: id→text captured from astral_recall results\n"
    "#          and (when the server returns per-memory text) from prefetch.\n"
    "#          A1 needs text; ids without text are UNSCORED for A1, still\n"
    "#          covered by A2.\n"
    "#   NEW  — sync_turn(): snapshots served ids for the turn, runs A1 against\n"
    "#          assistant_content, POSTs signals (pool, non-fatal), stashes\n"
    "#          {turn, served, prev_user} in _fidelity_pending[sid] for A2.\n"
    "#   NEW  — on_turn_start(): if pending, runs A2(message vs prev_user),\n"
    "#          POSTs, clears. Fires before the consolidation nudge.\n"
    "#   NEW  — config key `fidelity` (default true). Plugin-side gate only —\n"
    "#          the server's response_fidelity.observation_mode is the real\n"
    "#          safety and ships ON. Payload never contains memory text.\n"
    "#   FIX  — version 2.8.0 -> 2.9.0 (manifest bumped in step).\n"
    "#\n"
    "# v2.8.0 CHANGES (P-2 provenance rendering + A3-3 session_scope):\n",
))

# 2. version + soft import
EDITS.append((
    '_VERSION = "2.8.0"\n',
    '_VERSION = "2.9.0"\n',
))
EDITS.append((
    "from . import schemas\n",
    "from . import schemas\n"
    "\n"
    "# v2.9.0 (RT-13 F2): soft import — the provider must keep working if the\n"
    "# fidelity module is missing from an install (older copy, partial deploy).\n"
    "try:  # pragma: no cover\n"
    "    from . import fidelity as _fidelity\n"
    "except Exception:  # noqa: BLE001\n"
    "    _fidelity = None\n",
))

# 3. state fields
EDITS.append((
    "        self._recalled_ids: Dict[str, List[str]] = {}\n",
    "        self._recalled_ids: Dict[str, List[str]] = {}\n"
    "\n"
    "        # v2.9.0 (RT-13 F2): id→text for memories served this session, so\n"
    "        # A1 can score evidence-use. Bounded per session (last 64).\n"
    "        self._served_texts: Dict[str, Dict[str, str]] = {}\n"
    "        # v2.9.0 (RT-13 F2): per-session pending A2 context, written by\n"
    "        # sync_turn, consumed by the next on_turn_start.\n"
    "        # {sid: {\"turn\": int, \"served\": [ids], \"prev_user\": str}}\n"
    "        self._fidelity_pending: Dict[str, Dict[str, Any]] = {}\n",
))
EDITS.append((
    '        self._session_scope: Optional[str] = None  # v2.8.0 (A3-3): "verification" or None\n',
    '        self._session_scope: Optional[str] = None  # v2.8.0 (A3-3): "verification" or None\n'
    "        self._fidelity_enabled: bool = True  # v2.9.0 (RT-13 F2)\n",
))

# 4. config load
EDITS.append((
    '        scope = str(cfg.get("session_scope", "") or "").strip().lower()\n',
    '        # v2.9.0 (RT-13 F2): plugin-side gate. Server observation mode is\n'
    '        # the safety; this only lets an operator silence the POSTs.\n'
    '        self._fidelity_enabled = bool(cfg.get("fidelity", self._fidelity_enabled))\n'
    '        if _fidelity is None and self._fidelity_enabled:\n'
    '            logger.info("astral-memory: fidelity module absent; RT-13 signals disabled")\n'
    '            self._fidelity_enabled = False\n'
    '\n'
    '        scope = str(cfg.get("session_scope", "") or "").strip().lower()\n',
))

# 5. config schema entry (after session_scope entry)
EDITS.append((
    '                "key": "session_scope",\n'
    '                "description": (\n'
    '                    "Optional. Set to \'verification\' for a Hermes instance used "\n'
    '                    "to run fixtures/canaries; ingested memories are tagged so "\n'
    '                    "the server can down-rank them (A3-3). Leave empty otherwise."\n'
    '                ),\n'
    '                "default": "",\n'
    '            },\n',
    '                "key": "session_scope",\n'
    '                "description": (\n'
    '                    "Optional. Set to \'verification\' for a Hermes instance used "\n'
    '                    "to run fixtures/canaries; ingested memories are tagged so "\n'
    '                    "the server can down-rank them (A3-3). Leave empty otherwise."\n'
    '                ),\n'
    '                "default": "",\n'
    '            },\n'
    '            {\n'
    '                "key": "fidelity",\n'
    '                "description": (\n'
    '                    "Send RT-13 response-fidelity signals (ids only, no text) "\n'
    '                    "to the server after each turn. The server decides whether "\n'
    '                    "to apply or only log them (observation mode)."\n'
    '                ),\n'
    '                "default": "true",\n'
    '                "choices": ["true", "false"],\n'
    '            },\n',
))

# 6. _tool_recall: capture id→text from results (inside the existing try)
EDITS.append((
    "                if tagged:\n"
    "                    data[\"provenance_legend\"] = _PROVENANCE_LEGEND\n",
    "                if tagged:\n"
    "                    data[\"provenance_legend\"] = _PROVENANCE_LEGEND\n"
    "                # v2.9.0 (RT-13 F2): remember id→text so A1 can score\n"
    "                # evidence-use when this turn is synced.\n"
    "                self._remember_served(session_id, results)\n",
))

# 7. prefetch: capture id→text if the server returns per-memory objects
EDITS.append((
    "        ids = data.get(\"memory_ids\")\n"
    "        if isinstance(ids, list):\n"
    "            with self._state_lock:\n"
    "                self._recalled_ids[session_id] = [\n"
    "                    str(i) for i in ids if i\n"
    "                ][:32]\n",
    "        ids = data.get(\"memory_ids\")\n"
    "        if isinstance(ids, list):\n"
    "            with self._state_lock:\n"
    "                self._recalled_ids[session_id] = [\n"
    "                    str(i) for i in ids if i\n"
    "                ][:32]\n"
    "        # v2.9.0 (RT-13 F2): if the server also returns per-memory objects\n"
    "        # (any of memories/results/items with id+text), keep the text for\n"
    "        # A1. Older servers return ids only → those stay UNSCORED for A1.\n"
    "        for key in (\"memories\", \"results\", \"items\"):\n"
    "            objs = data.get(key)\n"
    "            if isinstance(objs, list) and objs:\n"
    "                self._remember_served(session_id, objs)\n"
    "                break\n",
))

# 8. sync_turn: A1 + pending snapshot (after ns resolve, before def _sync)
EDITS.append((
    "        sid = self._sid(session_id)\n"
    "\n"
    "        # §10.1: resolve namespace for auto-capture.\n"
    "        ns = self._resolve_namespace()\n"
    "\n"
    "        def _sync() -> None:\n",
    "        sid = self._sid(session_id)\n"
    "\n"
    "        # §10.1: resolve namespace for auto-capture.\n"
    "        ns = self._resolve_namespace()\n"
    "\n"
    "        # v2.9.0 (RT-13 F2): fidelity Layer A, same turn. Snapshot the\n"
    "        # served ids NOW (prefetch for the next turn will overwrite them),\n"
    "        # score A1 against the assistant text, and stash the A2 context.\n"
    "        self._fidelity_after_turn(sid, user_content, assistant_content)\n"
    "\n"
    "        def _sync() -> None:\n",
))

# 9. on_turn_start: A2 before consolidation nudge
EDITS.append((
    "    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:\n"
    "        sid = self._sid(kwargs.get(\"session_id\", \"\"))\n"
    "        with self._state_lock:\n"
    "            self._turn_counts[sid] = turn_number\n"
    "\n",
    "    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:\n"
    "        sid = self._sid(kwargs.get(\"session_id\", \"\"))\n"
    "        with self._state_lock:\n"
    "            self._turn_counts[sid] = turn_number\n"
    "\n"
    "        # v2.9.0 (RT-13 F2): fidelity Layer A2 — the user's next message\n"
    "        # tells us whether the previous response landed.\n"
    "        self._fidelity_next_turn(sid, message)\n"
    "\n",
))

# 10. helper methods — insert before `def _ready(`
EDITS.append((
    "    def _ready(self) -> bool:\n",
    "    # ------------------------------------------------------------------\n"
    "    # RT-13 Layer A — response fidelity (v2.9.0, Amendment A5 §2)\n"
    "    # ------------------------------------------------------------------\n"
    "\n"
    "    def _remember_served(self, session_id: str, objs: Any) -> None:\n"
    "        \"\"\"Capture id→text for served memories (bounded, non-fatal).\"\"\"\n"
    "        try:\n"
    "            sid = self._sid(session_id)\n"
    "            with self._state_lock:\n"
    "                bucket = self._served_texts.setdefault(sid, {})\n"
    "                for o in objs or []:\n"
    "                    if not isinstance(o, dict):\n"
    "                        continue\n"
    "                    mid = o.get(\"id\") or o.get(\"memory_id\")\n"
    "                    if not mid:\n"
    "                        continue\n"
    "                    text = o.get(\"text\") or o.get(\"content\") or o.get(\"summary\") or \"\"\n"
    "                    bucket[str(mid)] = str(text)[:2000]\n"
    "                if len(bucket) > 64:\n"
    "                    for k in list(bucket)[:-64]:\n"
    "                        bucket.pop(k, None)\n"
    "        except Exception as e:  # noqa: BLE001\n"
    "            logger.debug(\"fidelity: _remember_served skipped: %s\", e)\n"
    "\n"
    "    def _fidelity_post(self, sid: str, turn: int, signals: list) -> None:\n"
    "        \"\"\"Build and POST a fidelity batch on the pool. Never raises.\"\"\"\n"
    "        if not signals or _fidelity is None or not self._ready():\n"
    "            return\n"
    "        payload = _fidelity.build_payload(\n"
    "            sid, turn, signals, source=f\"hermes-{_VERSION}\",\n"
    "            cosine_available=False,\n"
    "        )\n"
    "        if not payload:\n"
    "            return\n"
    "\n"
    "        def _send() -> None:\n"
    "            r = self._http.post(\"/v1/memories/fidelity\", payload)\n"
    "            if \"error\" in r:\n"
    "                logger.debug(\"fidelity POST failed (non-fatal): %s\", r[\"error\"])\n"
    "            else:\n"
    "                logger.debug(\"fidelity: applied=%s logged=%s obs=%s rejected=%s\",\n"
    "                             r.get(\"applied\"), r.get(\"logged\"),\n"
    "                             r.get(\"observation_mode\"), len(r.get(\"rejected\") or []))\n"
    "\n"
    "        self._submit(_send)\n"
    "\n"
    "    def _fidelity_after_turn(self, sid: str, user_content: str,\n"
    "                             assistant_content: str) -> None:\n"
    "        \"\"\"A1 (evidence-use) for the turn just completed + stash A2 context.\"\"\"\n"
    "        if not self._fidelity_enabled or _fidelity is None:\n"
    "            return\n"
    "        try:\n"
    "            with self._state_lock:\n"
    "                served_ids = list(self._recalled_ids.get(sid, []))\n"
    "                texts = dict(self._served_texts.get(sid, {}))\n"
    "                turn = int(self._turn_counts.get(sid, 0))\n"
    "                # Explicit astral_recall results are served too, even if\n"
    "                # the server's augmented-prompt didn't list them.\n"
    "                for mid in texts:\n"
    "                    if mid not in served_ids:\n"
    "                        served_ids.append(mid)\n"
    "                served_ids = served_ids[:32]\n"
    "                self._fidelity_pending[sid] = {\n"
    "                    \"turn\": turn,\n"
    "                    \"served\": served_ids,\n"
    "                    \"prev_user\": (user_content or \"\")[:2000],\n"
    "                }\n"
    "            if not served_ids:\n"
    "                return\n"
    "            served = [_fidelity.ServedMemory(i, texts.get(i, \"\")) for i in served_ids]\n"
    "            a1 = _fidelity.a1_evidence_use(served, assistant_content or \"\")\n"
    "            logger.debug(\"fidelity A1 turn=%s served=%d verdicts=%s\",\n"
    "                         turn, len(served_ids), a1.per_memory)\n"
    "            self._fidelity_post(sid, turn, a1.signals)\n"
    "        except Exception as e:  # noqa: BLE001 — never let feedback kill a turn\n"
    "            logger.debug(\"fidelity A1 skipped: %s\", e)\n"
    "\n"
    "    def _fidelity_next_turn(self, sid: str, message: str) -> None:\n"
    "        \"\"\"A2 (continuation/correction) for the previous turn's served ids.\"\"\"\n"
    "        if not self._fidelity_enabled or _fidelity is None:\n"
    "            return\n"
    "        try:\n"
    "            with self._state_lock:\n"
    "                pending = self._fidelity_pending.pop(sid, None)\n"
    "            if not pending or not pending.get(\"served\"):\n"
    "                return\n"
    "            a2 = _fidelity.a2_next_turn(pending[\"served\"], message or \"\",\n"
    "                                        pending.get(\"prev_user\", \"\"))\n"
    "            logger.debug(\"fidelity A2 turn=%s verdict=%s reason=%s\",\n"
    "                         pending[\"turn\"], a2.verdict, a2.reason)\n"
    "            self._fidelity_post(sid, int(pending[\"turn\"]), a2.signals)\n"
    "        except Exception as e:  # noqa: BLE001\n"
    "            logger.debug(\"fidelity A2 skipped: %s\", e)\n"
    "\n"
    "    def _ready(self) -> bool:\n",
))

# ── Preflight ────────────────────────────────────────────────────────────────
bad = []
for i, (anchor, _) in enumerate(EDITS, 1):
    n = src.count(anchor)
    if n != 1:
        bad.append(f"  edit #{i}: anchor count = {n} (want 1)")
if man.count("version: 2.8.0\n") != 1:
    bad.append("  plugin.yaml: 'version: 2.8.0' count != 1")
if bad:
    print("PREFLIGHT FAILED — nothing written:\n" + "\n".join(bad))
    sys.exit(1)

# ── Backup ───────────────────────────────────────────────────────────────────
for p in (init, manifest):
    shutil.copy2(p, p.with_suffix(p.suffix + ".bak-pre-f2"))

# ── Apply ────────────────────────────────────────────────────────────────────
out = src
for anchor, repl in EDITS:
    out = out.replace(anchor, repl, 1)
init.write_text(out, encoding="utf-8")

man_out = man.replace("version: 2.8.0\n", "version: 2.9.0\n", 1)
man_out = man_out.replace(
    "# Generation Timestamp: 2026-08-17T09:15:00Z\n",
    "# Generation Timestamp: 2026-08-18T22:10:00Z\n"
    "# v2.9.0: RT-13 Layer A fidelity feedback (fidelity.py + hooks). No new\n"
    "#         tools; provides_tools/hooks unchanged. Bumped in step with _VERSION.\n",
    1,
)
manifest.write_text(man_out, encoding="utf-8")

# ── Post-verify ──────────────────────────────────────────────────────────────
import ast  # noqa: E402
try:
    ast.parse(out)
except SyntaxError as e:
    # roll back
    shutil.copy2(init.with_suffix(".py.bak-pre-f2"), init)
    shutil.copy2(manifest.with_suffix(".yaml.bak-pre-f2"), manifest)
    sys.exit(f"POST-VERIFY FAILED (syntax) — rolled back: {e}")

checks = {
    "_VERSION 2.9.0": '_VERSION = "2.9.0"' in out,
    "soft import": "from . import fidelity as _fidelity" in out,
    "state fields": "_fidelity_pending" in out and "_served_texts" in out,
    "config key": '"key": "fidelity"' in out,
    "sync_turn hook": "self._fidelity_after_turn(sid, user_content, assistant_content)" in out,
    "on_turn_start hook": "self._fidelity_next_turn(sid, message)" in out,
    "helpers": out.count("def _fidelity_") == 3 and "def _remember_served" in out,
    "endpoint": "/v1/memories/fidelity" in out,
    "manifest 2.9.0": "version: 2.9.0" in man_out,
}
failed = [k for k, v in checks.items() if not v]
if failed:
    shutil.copy2(init.with_suffix(".py.bak-pre-f2"), init)
    shutil.copy2(manifest.with_suffix(".yaml.bak-pre-f2"), manifest)
    sys.exit(f"POST-VERIFY FAILED {failed} — rolled back")

print("F2 applied: __init__.py v2.9.0, plugin.yaml 2.9.0. Backups: *.bak-pre-f2")
print("Next: python3 %s  (self-test)  |  hermes memory list  (expect 2.9.0)" % fid)
