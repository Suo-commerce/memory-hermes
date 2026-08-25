# Generation Timestamp: 2026-08-25T12:00:00Z
# Purpose: Hermes memory plugin — v2.9.1 routes astral_store to the direct
#          /v1/memory/add endpoint so explicit stores land verbatim (no
#          dyad extraction, no surprise gate, category + namespace as real
#          fields); v2.9.0 added RT-13 Layer A response-fidelity feedback
#          (SPEC-REMEMBERING-TOUCH-001 Amendment A5 §2).
#
# v2.9.1 CHANGES (STORE-1 — explicit store bypasses the extractor):
#   FIX  — _tool_store POSTed a synthetic dialogue ({user_message: text,
#          assistant_response: "Stored as <category>."}) to
#          /v1/memory/ingest. The server ran dyad extraction on it and
#          persisted the Dreamer's derived observations (source_role=dyad,
#          extraction_version=dyad-v1.2) instead of the caller's text, and
#          reported segments_stored=0. Finnish input was paraphrased into
#          English; `category` never reached the server as a field.
#   NEW  — _tool_store now POSTs {text, category, source, [namespace],
#          metadata:{source, category}} to /v1/memory/add — the same
#          direct path _pref_declare has used since v2.7. Verified on
#          bot-jarmo 2026-08-25: verbatim text, discovery_origin="direct",
#          source_role=null, namespace honoured, no surprise gate.
#   NEW  — Normalised tool response: {success, id, text, category,
#          namespace, total_memories} instead of the raw ingest counters.
#   KEEP — sync_turn, session and batch ingest stay on /v1/memory/ingest;
#          conversation history is the correct input for the extractor.
#   TEST — tests/test_provider_contract.py store tests (lines ~754-777)
#          must assert on /v1/memory/add (next file).
#   FIX  — version 2.9.0 -> 2.9.1 (manifest plugin.yaml bumped in step).
#
# v2.9.0 CHANGES (RT-13 Layer A — Amendment A5 §2, F2):
#   NEW  — fidelity.py (F1) soft-imported; two pure classifiers, no LLM.
#   NEW  — _served_texts[sid]: id→text captured from astral_recall results
#          and (when the server returns per-memory text) from prefetch.
#          A1 needs text; ids without text are UNSCORED for A1, still
#          covered by A2.
#   NEW  — sync_turn(): snapshots served ids for the turn, runs A1 against
#          assistant_content, POSTs signals (pool, non-fatal), stashes
#          {turn, served, prev_user} in _fidelity_pending[sid] for A2.
#   NEW  — on_turn_start(): if pending, runs A2(message vs prev_user),
#          POSTs, clears. Fires before the consolidation nudge.
#   NEW  — config key `fidelity` (default true). Plugin-side gate only —
#          the server's response_fidelity.observation_mode is the real
#          safety and ships ON. Payload never contains memory text.
#   FIX  — version 2.8.0 -> 2.9.0 (manifest bumped in step).
#
# v2.8.0 CHANGES (P-2 provenance rendering + A3-3 session_scope):
#   NEW  — astral_recall results carry a compact "provenance" tag built
#          from the server's source_role/content_class/user_intent
#          (top-level since R-5, also present in metadata): "[dyad] re: …",
#          "[assistant]", "[user]", "[diagnostic]"; legacy memories carry
#          no tag. A "provenance_legend" is attached once per response so
#          the agent applies "faithful, not factual": [assistant] is
#          distilled belief, [user] is ground truth, [dyad] is answer
#          anchored to a user question. Auto-prefetch context is rendered
#          server-side and unchanged here.
#   NEW  — config `session_scope` (optional; "verification" only). When
#          set, every ingest payload carries "session_scope" so the server
#          can down-rank verification-session memories (A3-3, explicit
#          client-set — the server does NOT infer scope from session ids).
#          Set it in astral-memory.json for a Hermes instance used to run
#          fixtures/canaries; leave unset for normal use.
#   CHG  — default max_recall_memories 5 → 8. With hybrid BM25+cosine
#          retrieval and provenance ranking (2026-08-17) the pool is worth
#          reading; 5 truncated payload behind stubs in Gate 2 (pool =
#          12 × max_memories). Existing astral-memory.json values win.
#   CHG  — default capture_max_chars 8000 → 12000 to match the server's
#          dyad_max_chars (SPEC-DYAD-DISTILLATION-001 §3.3). Previously the
#          plugin truncated long assistant answers before the server's
#          head+tail truncation could run, silently dropping the tail of
#          F-3/F-11-class exchanges without the [TRUNCATED] marker.
#
# astral-memory/__init__.py
# v2.7.2 CHANGES (R-ECHO-1b — INCIDENT-BOTJARMO-NULLBRAIN-001 N-11):
#   FIX  — sync_turn(): ingest payload now includes "session_id": sid
#          alongside the existing source=hermes_{sid}. Stored memories
#          previously carried NO session_id in metadata (only source), so
#          the server's same-session echo filter compared against an
#          absent field and silently no-op'd — prod evidence 2026-08-12:
#          the morning's question echoed itself at 0.89 in prefetch as 4
#          near-duplicate copies.
#   FIX  — _do_prefetch(): augmented-prompt payload now includes
#          "session_id" (same _sid() derivation as ingest — parity by
#          construction, the two can never drift). Requires server with
#          AugmentedPromptRequest.session_id (types.rs 2026-08-12);
#          harmless extra field on older servers (serde ignores it).
#
# v2.7.1 CHANGES (INCIDENT-BOTJARMO-NULLBRAIN-001 N-10 — pre-filter removal):
#   FIX  — sync_turn(): the v2.3.0 "<8 combined words" pre-filter is
#          removed (N-10a). Short turns are often the highest-signal ones
#          ("Mina the Hollower" = 3 words, dropped client-side, root cause
#          #4 of the Mina incident). SPEC-EPISODE-BOUNDARY-001 moves noise
#          filtering server-side (SEC-ORD-1: redact → spool-append → noise
#          → gate); a client word count violates the raw-turn spool's
#          completeness guarantee. Only whitespace-only turns are skipped.
#   FIX  — on_pre_compress(): the `len(user_text) > 20` char gate is
#          removed (N-10b) for the same reason; non-empty is sufficient.
#   NOTE — The v2.3.0 rationale ("junk feeds Dreamer episode generation")
#          is obsolete once the server-side spool ships; until then the
#          server's noise filter + surprise gate absorb the extra volume.
#          Rejected alternative: lowering the threshold to a smaller word
#          count — any count > 0 drops some class of proper-noun message.
#
# v2.7.0 CHANGES (SPEC-PREFERENCE-DISTILLATION-001 — plugin campaign):
#   NEW  — astral_preferences tool: 5 actions (pending/approve/decline/
#          withdraw/declare). The consent surface for H7-discovered and
#          user-declared preferences. Each action maps to server endpoints
#          shipped in the server campaign (list, PATCH, guarded add).
#   NEW  — Preference injection in _do_prefetch: second fetch via POST
#          /v1/memory/list for the preferences namespace; status == active
#          filtered client-side; rendered as a distinct [USER PREFERENCES]
#          block in canonical form (one sentence each, ≤140 chars). Total
#          injection (no rotation) — the §4 cap guarantees ≤1500 chars.
#   NEW  — Injection trace: DEBUG log per turn with injected preference
#          IDs, count, chars. The §5.2 requirement and the six-silent-
#          failures lesson applied prophylactically.
#   NEW  — system_prompt_block mentions preferences when active.
#
# v2.5.3 CHANGES (SPEC-PLUGIN-SESSION-END-002 v1.0.0 — sixth broken link):
#   FIX  — v2.5.2's session-end notification fired correctly, but the
#          Dreamer's corpus lookup for the session's turns SILENTLY found
#          zero: the server assigns its own internal session_id on ingest
#          (incrementing integer) which never matches the plugin's Hermes
#          session UUID. Event fired, Dreamer woke, looked up the wrong
#          name, produced nothing, logged nothing. (Diagnosed by the
#          Hermes Agent, 2026-08-02.)
#   NEW  — on_session_end() now calls POST /v1/episodes/generate DIRECTLY
#          with the full transcript (>=3 turns) — the reliable path that
#          carries its own turns instead of trusting an ID lookup.
#          Turn shape per server types.rs EpisodeTurn: {"text", "speaker"}
#          (spec's locked assumption 2 said {"role","text"} — corrected).
#   KEPT — POST /v1/memory/session-end still fired afterward (Dreamer
#          event for timeline markers / HOPE signals; no longer the
#          episode-text path — logging downgraded INFO→DEBUG on success).
#   KEPT — diary write unchanged, last.
#   ORDER — episodes/generate FIRST (the valuable artifact), then
#          session-end, then diary; all synchronous (pool may be torn
#          down right after this hook).
#   SRV-2 (server follow-up, out of plugin scope): ingest should persist
#          the caller-supplied session_id so Dreamer ID-based collection
#          works for all clients; until then every client of session-end
#          silently generates nothing.
#
# v2.5.2 CHANGES (fifth broken link — T6 trigger):
#   FIX  — on_session_end() now ALSO calls POST /v1/memory/session-end,
#          which fires DreamerEvent::SessionCompleted → T6 /
#          run_dream_shark() → episode summary + timeline markers.
#          Previously the hook only wrote the diary entry; the Dreamer
#          never learned any session had ended, so T6 has never fired in
#          production. (Historical note: this exact chain was previously
#          broken on the SERVER side — the endpoint didn't exist and
#          astral-session-end.sh silently 404'd, fixed in server v14.
#          Both ends have now failed independently. The chain deserves a
#          liveness invariant: episodes_generated > 0 after N sessions.)
#          Contract per server types.rs: {session_id?, project_dir?} —
#          NO turns array; the Dreamer collects the session's turn
#          memories from the corpus (per-turn ingests already stored
#          them). session-end is fired BEFORE the diary write so the
#          Dreamer event is not lost if the diary call raises during
#          pool teardown.
#
# v2.5.1 CHANGES (GW-1 follow-ups):
#   FIX  — health() now calls /v1/health (authenticated) instead of the
#          public /health. SEC-FIX-3 deliberately trimmed the public
#          endpoint to liveness-only (no corpus stats leak unauthenticated);
#          the plugin was reading the trimmed body and displaying
#          "0 memories". The plugin has the Bearer token — it uses the
#          protected endpoint that carries total_memories + feature flags.
#          Falls back to public /health when no token is configured
#          (unauth'd servers keep working, liveness still detected).
#   FIX  — Default server_url is http://127.0.0.1:8090 (was localhost).
#          Python resolves localhost to ::1 first; the server binds IPv4
#          only. The resulting connection failures tripped the circuit
#          breaker in 60s waves since 2026-07-29. Explicit IPv4 removes
#          the resolver from the failure surface. Existing configs with
#          "localhost" are rewritten to 127.0.0.1 at load (same host,
#          zero behavior change on IPv4-only setups, cures dual-stack).
#   NEW  — ConnectionError is logged at WARNING with the target URL, once
#          per breaker cycle (first failure + breaker-open). Connection
#          failures were previously silent until the breaker warning.
#
# v2.5.0 CHANGES (GW-1 root-cause fix — Bearer authentication):
#   FIX  — The plugin sent NO Authorization header on any request. When the
#          server began enforcing Bearer auth, every call started failing
#          with 401 — swallowed silently by the framework's MemoryManager.
#          Confirmed on the wire (tcpdump, 2026-08-01): POST → 401, no
#          Bearer header present. The `api_token` key in astral-memory.json
#          was read by nobody. Signal memory capture was dead the entire
#          time while the agent answered fluently.
#   NEW  — _HttpClient sends `Authorization: Bearer <token>` on every
#          request (including health) when a token is configured.
#   NEW  — `api_token` read from astral-memory.json in _load_config();
#          ASTRAL_API_TOKEN env var overrides (parallel to ASTRAL_SERVER_URL).
#   NEW  — 401/403 are logged at ERROR, always. Auth failure must never be
#          silent again; the silence cost a full evening of forensics and an
#          unknown span of lost memories.
#   NEW  — One-shot token refresh on 401: the client re-reads the config
#          file once and retries, making server-side token rotation
#          self-healing without a gateway restart.
#   NEW  — Prefetch consumes `namespace_fallback` from the server response
#          (NS-1, server >= this deploy): when the requested namespace had
#          zero results and the server fell back to cross-namespace, the
#          injected context block is labeled so the agent knows. Graceful
#          on older servers (key absent → no label).
#   NOTE — 401/403 do NOT trip the circuit breaker (HTTPError branch already
#          calls _record_success(); preserved). Auth-broken ≠ server-down.
#
# v2.4.0 CHANGES (SPEC-NAMESPACE-001 v1.0 — Memory Namespace Architecture):
#   NEW  — Namespace support. Memories are partitioned by context (project,
#          topic, game world) via a slug string. The server stores and filters
#          on it; the plugin tracks which namespace is active for a session.
#          §7-§15 of the consolidated spec.
#   NEW  — `default_namespace` config key in astral-memory.json. Empty or
#          absent → cross-namespace mode (all namespaces searched, writes go
#          to server default "default"). Non-empty → sent as `namespace` on
#          writes and `namespace_filter` on reads, unless overridden per-call.
#   NEW  — `_active_namespace` / `_config_namespace` state fields with
#          `_resolve_namespace()` resolution method (§7.2, §9.1).
#   NEW  — `astral_namespace` tool: set / get / clear the active namespace
#          for the current session (§12). Registered in both dispatch and
#          get_tool_schemas() so the LLM can discover and invoke it.
#   NEW  — `namespace` parameter on `astral_recall` and `astral_store` for
#          per-call overrides (§13). `astral_recall` with namespace="all"
#          forces cross-namespace search.
#   NEW  — Feature detection: `self._server_supports_namespace` probed at
#          startup via the /health endpoint. When False, all namespace params
#          are silently omitted (§14.1). Old servers keep working.
#   NEW  — Namespace injected into all write paths: sync_turn, _tool_store,
#          on_pre_compress, on_delegation, on_memory_write (§10).
#   NEW  — namespace_filter injected into all read paths: _do_prefetch,
#          _tool_recall (§11). Briefings stay cross-namespace by design.
#   NEW  — `_active_namespace` reset to `_config_namespace` on session switch
#          (§9.2). Subagents inherit the parent's namespace via on_delegation
#          (§9.3).
#
# v2.3.1 CHANGES (SPEC-HERMES-TUNING-001 T3 — circuit breaker fix):
#   FIX — requests.Timeout now calls _record_success() instead of
#     _record_failure(). A timeout means the server accepted the connection
#     and is processing — it's alive but slow (e.g., D8 reembed saturating
#     LanceDB I/O). This matches the existing HTTPError treatment (line 195:
#     "Server is alive — it just said no. Don't trip the breaker."). Before
#     this fix: 3 × 10s timeouts during D8 → CB opens 60s → retries → 3
#     more timeouts → opens again, cycling for the entire D8 duration
#     (potentially hours). Writes silently dropped the whole time.
#     ConnectionError still trips the CB (server actually unreachable).
#
# v2.3.0 CHANGES (noise pre-filter + delegation provenance):
#   NEW — sync_turn() pre-filter: turns whose combined content is under 8
#     words are not ingested. "Thanks!" / "You're welcome" / short bash
#     acknowledgements carry nothing extractable; auto-capturing them was
#     the plugin-side contributor to corpus noise (production finding
#     RC-3 adjacent — the server's surprise gate catches most, but junk
#     input also feeds Dreamer episode generation downstream).
#   NEW — _recalled_ids: per-session tracking of which memory IDs the
#     server injected via /v1/memory/augmented-prompt. Requires server
#     >= v2.12.x with `memory_ids` in AugmentedPromptResponse; degrades
#     to a no-op on older servers (field absent -> empty list).
#   NEW — on_delegation() now attaches the parent session's recalled
#     memory IDs as `context_manifest` provenance metadata on the
#     delegation-result ingest. The sub-agent still runs with
#     skip_memory=True (Hermes architecture — no pre-delegation hook
#     exists), so this is retrospective linkage, not live handoff: the
#     consolidated result enters the corpus already tied to the parent
#     context that produced it. Live handoff needs an upstream Hermes
#     `on_before_delegation` hook; tracked in SPEC-AGENT-HOOKS-001 v1.5
#     §8.4.
#   Session hygiene — _recalled_ids follows the same lifecycle as
#     _prefetch_cache (cleared on reset, dropped across id boundaries).
#
# v2.2.0 CHANGES (contract repair for Hermes Agent >= v2026.7.1 / v0.18.0):
#   BREAKING FIX — prefetch()/queue_prefetch()/sync_turn() now accept the
#     keyword-only `session_id` argument that MemoryManager passes on every
#     call. Under v2.2.0 of Hermes these raised TypeError, which the manager
#     swallowed at DEBUG (prefetch) and WARNING (sync_turn) level. Net effect:
#     auto-recall and auto-capture were silently dead. This is the bug.
#   BREAKING FIX — handle_tool_call() renamed first param to `tool_name` and
#     now accepts **kwargs, matching the ABC.
#   FIX — on_pre_compress() now returns str (contribution to the compression
#     summary prompt) instead of None.
#   FIX — removed the MemoryProvider fallback stub. It froze the v0.17-era
#     contract and made this exact drift invisible during local testing.
#     A missing `agent.memory_provider` is now a loud ImportError.
#   NEW — on_session_switch(): resets/reassigns per-session state. Previously
#     `_session_id` was captured once in initialize() and never updated, so
#     every write after a /new, /reset, /branch, /resume or context compression
#     was tagged with a stale source id. Silent corpus pollution.
#   NEW — on_delegation(): subagents run with skip_memory=True, so the parent
#     provider is the only place delegation results can land. They were
#     previously discarded.
#   NEW — on_turn_start(): turn counter + periodic consolidation trigger.
#   NEW — backup_paths(): declares the Astral data dir so `hermes backup`
#     captures the corpus. Callable without initialize() and without network.
#   NEW — on_memory_write() accepts the `metadata` provenance dict.
#   NEW — _assert_contract(): introspects the live ABC at register() time and
#     logs a loud ERROR on any signature drift, so the next breaking change
#     surfaces as a startup error rather than a support ticket.
#   CHANGE — prefetch cache is now keyed by session_id (was a single slot;
#     concurrent gateway sessions could read each other's context).
#   CHANGE — unbounded per-turn thread spawning replaced with a bounded
#     ThreadPoolExecutor(max_workers=2).
#   CHANGE — config keys documented in the README are now actually read:
#     briefing_on_start, min_similarity, data_dir.
#   REMOVED — nothing. tools.py deletion is a separate change.
"""
Astral Core Memory — Hermes Agent Memory Provider Plugin
Version: 2.4.0

Offline-first persistent memory with surprise-gated learning.
Implements the MemoryProvider ABC for proper Hermes integration.

Install:
  Copy this directory to <hermes-agent>/plugins/memory/astral_memory/
  (underscore — Hermes matches memory.provider against the directory name)

  hermes config set memory.provider astral_memory

Requires:
  Hermes Agent >= v2026.7.1 (v0.18.0). Earlier releases use a different
  MemoryProvider signature; pin astral-memory-hermes==2.1.0 for those.

Architecture:
  Thin HTTP bridge -> Astral Core memory server (:8090).
  The server runs the full MASK/HOPE pipeline — surprise gating,
  Delta Rule matrix, 5-tier lifecycle, hybrid retrieval (HyDE +
  cross-encoder reranking + session expansion), Dreamer consolidation.
  This plugin just talks to it.

Namespace (SPEC-NAMESPACE-001):
  Memories are partitioned by context via a namespace slug. The plugin
  tracks the active namespace per session; the server stores and filters
  on the string. Set via the `astral_namespace` tool, the
  `default_namespace` config key, or per-call on `astral_recall` /
  `astral_store`. When no namespace is active, all namespaces are
  searched (cross-namespace mode).

Repository: https://github.com/Suo-commerce/memory-hermes
License: MIT
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Hard import. If this fails the plugin is installed against an
# incompatible Hermes and must not silently degrade — see v2.1.0, where a
# fallback stub encoding the old contract hid a total memory outage.
from agent.memory_provider import MemoryProvider

from . import schemas

# v2.9.0 (RT-13 F2): soft import — the provider must keep working if the
# fidelity module is missing from an install (older copy, partial deploy).
try:  # pragma: no cover
    from . import fidelity as _fidelity
except Exception:  # noqa: BLE001
    _fidelity = None

logger = logging.getLogger("astral-memory")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERSION = "2.9.1"
_DEFAULT_SERVER_URL = "http://127.0.0.1:8090"

# v2.8.0 (P-2): how to read a provenance tag. Attached once per recall
# response when at least one result carries provenance.
_PROVENANCE_LEGEND = (
    "[user] = the user's own statement (ground truth about their world); "
    "[dyad] = an answer anchored to a user question ('re:' shows the intent); "
    "[assistant] = distilled belief the assistant produced — faithful to what "
    "was said, not verified; [diagnostic] = notes about the memory system's "
    "own behaviour on a named item (usually not what the user is asking for); "
    "untagged = legacy memory of unknown provenance."
)


def _provenance_tag(result: Any) -> str:
    """Build a compact provenance tag from a search result, or '' for legacy."""
    if not isinstance(result, dict):
        return ""
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    role = result.get("source_role") or meta.get("source_role") or ""
    cls = result.get("content_class") or meta.get("content_class") or ""
    intent = result.get("user_intent") or meta.get("user_intent") or ""
    role = str(role).lower(); cls = str(cls).lower()
    if not role or role == "unknown":
        return ""
    if cls == "diagnostic":
        return "[diagnostic]"
    if role == "dyad":
        return f"[dyad] re: {str(intent)[:80]}" if intent else "[dyad]"
    if role in ("assistant", "user"):
        return f"[{role}]"
    return ""
_DEFAULT_DATA_DIR = "~/.astral"

_CONNECT_TIMEOUT = 3.0
_REQUEST_TIMEOUT = 10.0

# Circuit breaker — stop hammering a dead server
_CB_THRESHOLD = 3
_CB_RESET_SECONDS = 60

# Background work: prefetch + capture. Two is enough; more just queues
# writes behind a wedged server.
_MAX_WORKERS = 2

# Consolidation nudge cadence (turns). 0 disables.
_CONSOLIDATE_EVERY_N_TURNS = 50

# Namespace validation (SPEC-NAMESPACE-001 §3.2, §8.2)
_NS_MAX_LEN = 64


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

class _HttpClient:
    """HTTP client with a thread-safe circuit breaker."""

    def __init__(self, base_url: str, api_token: str = "",
                 token_loader=None):
        self.base_url = base_url.rstrip("/")
        self._lock = threading.Lock()
        self._failures = 0
        self._circuit_open_at: float = 0.0
        # v2.5.0: Bearer auth. token_loader is an optional zero-arg callable
        # returning the current token from config — used for the one-shot
        # refresh on 401 so token rotation self-heals.
        self._api_token = api_token or ""
        self._token_loader = token_loader

    def _headers(self) -> Optional[dict]:
        if self._api_token:
            return {"Authorization": f"Bearer {self._api_token}"}
        return None

    def _refresh_token(self) -> bool:
        """Re-read the token via token_loader. True if it changed."""
        if self._token_loader is None:
            return False
        try:
            fresh = str(self._token_loader() or "")
        except Exception:  # noqa: BLE001
            return False
        if fresh and fresh != self._api_token:
            self._api_token = fresh
            logger.warning("Auth token refreshed from config after 401")
            return True
        return False

    def _circuit_open(self) -> bool:
        with self._lock:
            if self._failures < _CB_THRESHOLD:
                return False
            if time.monotonic() - self._circuit_open_at > _CB_RESET_SECONDS:
                self._failures = 0
                return False
            return True

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures == _CB_THRESHOLD:
                self._circuit_open_at = time.monotonic()
                logger.warning(
                    "Circuit breaker open — suppressing requests to %s for %ds",
                    self.base_url, _CB_RESET_SECONDS,
                )

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0

    def _request(self, method: str, path: str, *,
                 payload: Optional[dict] = None,
                 params: Optional[dict] = None,
                 timeout: float = _REQUEST_TIMEOUT) -> dict:
        if self._circuit_open():
            return {"error": "circuit_breaker_open"}
        try:
            r = requests.request(
                method,
                f"{self.base_url}{path}",
                json=payload if method in ("POST", "PUT", "PATCH") else None,
                params=params,
                headers=self._headers(),  # v2.5.0: Bearer auth
                timeout=timeout,
            )
            # v2.5.0: one-shot token refresh + retry on auth failure —
            # server-side token rotation self-heals without a restart.
            if r.status_code in (401, 403) and self._refresh_token():
                r = requests.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload if method in ("POST", "PUT", "PATCH") else None,
                    params=params,
                    headers=self._headers(),
                    timeout=timeout,
                )
            r.raise_for_status()
            self._record_success()
            return r.json()
        except requests.ConnectionError:
            # v2.5.1: log the first failure of a breaker cycle with the
            # target URL — connection failures (e.g. the localhost/IPv6
            # class) were invisible until the breaker-open warning.
            with self._lock:
                first_of_cycle = self._failures == 0
            if first_of_cycle:
                logger.warning(
                    "Connection to memory server failed: %s%s",
                    self.base_url, path,
                )
            self._record_failure()
            return {"error": f"Memory server not reachable at {self.base_url}"}
        except requests.Timeout:
            # Server is alive — it accepted the connection, just slow
            # (e.g., D8 reembed saturating I/O). Don't trip the breaker.
            self._record_success()
            return {"error": f"Request timed out ({timeout}s)"}
        except requests.HTTPError as e:
            # Server is alive — it just said no. Don't trip the breaker.
            self._record_success()
            status = e.response.status_code
            if status in (401, 403):
                # v2.5.0: auth failure is NEVER silent. This exact silence
                # hid a dead capture pipeline for an unknown span of time.
                logger.error(
                    "Memory server rejected auth (HTTP %d) on %s %s — "
                    "check api_token in astral-memory.json against "
                    "the server's token file",
                    status, method, path,
                )
            try:
                body = e.response.json()
            except Exception:
                body = {"detail": e.response.text[:200]}
            return {"error": f"HTTP {status}", **body}
        except Exception as e:  # noqa: BLE001 — never let a bridge kill a turn
            self._record_failure()
            return {"error": str(e)}

    def get(self, path: str, params: Optional[dict] = None,
            timeout: float = _REQUEST_TIMEOUT) -> dict:
        return self._request("GET", path, params=params, timeout=timeout)

    def post(self, path: str, payload: Optional[dict] = None,
             timeout: float = _REQUEST_TIMEOUT) -> dict:
        return self._request("POST", path, payload=payload or {}, timeout=timeout)

    def delete(self, path: str, timeout: float = _REQUEST_TIMEOUT) -> dict:
        return self._request("DELETE", path, timeout=timeout)

    def health(self) -> Optional[dict]:
        """Health check with a short timeout. Returns None on failure.

        v2.5.1: uses the PROTECTED /v1/health when a token is configured —
        the public /health is deliberately liveness-only (SEC-FIX-3: no
        corpus stats leak unauthenticated), which is why the plugin used
        to display "0 memories". Tokenless configs fall back to the
        public endpoint for liveness.
        """
        path = "/v1/health" if self._api_token else "/health"
        try:
            r = requests.get(f"{self.base_url}{path}",
                             headers=self._headers(),
                             timeout=_CONNECT_TIMEOUT)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _config_path(hermes_home: str) -> Path:
    return Path(hermes_home).expanduser() / "astral-memory.json"


def _read_config(hermes_home: str) -> dict:
    """Read the native config file. Safe to call before initialize()."""
    path = _config_path(hermes_home)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not read config at %s: %s", path, e)
        return {}


def _default_hermes_home() -> str:
    return os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))


# ---------------------------------------------------------------------------
# Namespace validation (SPEC-NAMESPACE-001 §8.2)
# ---------------------------------------------------------------------------

def _validate_namespace(ns: str) -> Optional[str]:
    """Return an error message if the namespace is invalid, None if valid.

    Rules (§3.2): lowercase alphanumeric + hyphens only, 1-64 chars,
    must not start or end with a hyphen. The token "all" is accepted
    as a special value meaning cross-namespace search.
    """
    if not ns or len(ns) > _NS_MAX_LEN:
        return f"namespace must be 1-{_NS_MAX_LEN} characters"
    if ns == "all":
        return None  # special token, valid
    if not all(
        c.isascii() and (c.islower() or c.isdigit() or c == '-')
        for c in ns
    ):
        return "namespace must be lowercase alphanumeric with hyphens only"
    if ns.startswith('-') or ns.endswith('-'):
        return "namespace must not start or end with hyphen"
    return None


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class AstralCoreMemoryProvider(MemoryProvider):
    """
    Astral Core — offline-first memory provider for Hermes Agent.

    Talks to the Astral Core memory server over HTTP (localhost:8090).
    The server handles surprise gating, embeddings, retrieval, and
    everything else. This provider is a thin bridge.
    """

    def __init__(self) -> None:
        self._http: Optional[_HttpClient] = None
        self._hermes_home: str = _default_hermes_home()
        self._server_url: str = _DEFAULT_SERVER_URL
        self._server_healthy: bool = False

        # Session state. `_active_session_id` is the id Hermes last told us
        # about; call sites may override it per-call via session_id=.
        self._active_session_id: str = ""
        self._state_lock = threading.RLock()

        # Prefetch cache, keyed by session_id. A single shared slot leaked
        # context between concurrent gateway sessions in <= 2.1.0.
        self._prefetch_cache: Dict[str, str] = {}

        # Turn counters, keyed by session_id.
        self._turn_counts: Dict[str, int] = {}

        # Memory IDs injected into context this session, keyed by
        # session_id. Populated from `memory_ids` in the augmented-prompt
        # response (server >= v2.12.x); empty on older servers. Used as
        # context_manifest provenance on delegation-result ingests.
        self._recalled_ids: Dict[str, List[str]] = {}

        # v2.9.0 (RT-13 F2): id→text for memories served this session, so
        # A1 can score evidence-use. Bounded per session (last 64).
        self._served_texts: Dict[str, Dict[str, str]] = {}
        # v2.9.0 (RT-13 F2): per-session pending A2 context, written by
        # sync_turn, consumed by the next on_turn_start.
        # {sid: {"turn": int, "served": [ids], "prev_user": str}}
        self._fidelity_pending: Dict[str, Dict[str, Any]] = {}

        # Namespace state (SPEC-NAMESPACE-001 §9.1).
        # _config_namespace is loaded from `default_namespace` in config.
        # _active_namespace is set per-session via the astral_namespace tool
        # and reset to _config_namespace on session switch.
        self._active_namespace: str = ""
        self._config_namespace: str = ""

        # Feature flag: probed at startup via /health. When False, all
        # namespace params are silently omitted (§14.1).
        self._server_supports_namespace: bool = False

        self._pool: Optional[ThreadPoolExecutor] = None
        self._shutting_down = False

        # Config
        self._auto_capture: bool = True
        self._auto_recall: bool = True
        self._max_recall: int = 8          # v2.8.0: was 5
        self._capture_max_chars: int = 12000  # v2.8.0: match dyad_max_chars
        self._briefing_on_start: bool = False
        self._min_similarity: Optional[float] = None
        self._session_scope: Optional[str] = None  # v2.8.0 (A3-3): "verification" or None
        self._fidelity_enabled: bool = True  # v2.9.0 (RT-13 F2)
        self._data_dir: str = _DEFAULT_DATA_DIR
        self._api_token: str = ""  # v2.5.0: Bearer auth

    # ------------------------------------------------------------------
    # Identity & availability
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "astral-memory"

    def is_available(self) -> bool:
        """No network calls permitted here per the Hermes contract.
        Connectivity is probed in initialize()."""
        return True

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._hermes_home = kwargs.get("hermes_home") or _default_hermes_home()
        self._load_config()

        with self._state_lock:
            self._active_session_id = session_id
            self._prefetch_cache.clear()
            self._turn_counts.clear()
            self._recalled_ids.clear()
            # §9.2: reset active namespace to config default on session start.
            self._active_namespace = self._config_namespace

        self._pool = ThreadPoolExecutor(
            max_workers=_MAX_WORKERS,
            thread_name_prefix="astral-memory",
        )
        self._http = _HttpClient(
            self._server_url,
            api_token=self._api_token,
            # v2.5.0: one-shot refresh source — re-read config on 401.
            token_loader=lambda: _read_config(self._hermes_home).get(
                "api_token", ""
            ),
        )

        health = self._http.health()
        if health:
            self._server_healthy = True
            # §14.1: feature-detect namespace support. The updated server
            # reports `namespace_enabled` in its /health response. Old
            # servers omit it — namespace params are silently skipped.
            self._server_supports_namespace = bool(
                health.get("namespace_enabled", False)
            )
            if self._config_namespace and not self._server_supports_namespace:
                logger.debug(
                    "Server doesn't support namespace, skipping "
                    "(config default_namespace='%s' ignored)",
                    self._config_namespace,
                )
            logger.info(
                "Astral Core Memory v%s: %s (server v%s) — %d memories, "
                "embedding=%s, rerank=%s, hyde=%s, namespace=%s",
                _VERSION,
                health.get("status", "?"),
                health.get("version", "?"),
                health.get("total_memories", 0),
                health.get("embedding_backend", "?"),
                "on" if health.get("deep_rerank_enabled") else "off",
                "on" if health.get("hyde_enabled") else "off",
                "on" if self._server_supports_namespace else "off",
            )
        else:
            self._server_healthy = False
            self._server_supports_namespace = False
            logger.warning(
                "Astral Core memory server not reachable at %s. "
                "Memory is INACTIVE for this session. Start the services:\n"
                "  1. llama-server --model nomic-embed-text-v1.5.Q5_K_M.gguf "
                "--port 8081 --embedding -b 4096 -ub 4096\n"
                "  2. python3 rerank_server.py --port 8082\n"
                "  3. ./astral-memory-server --embed-url http://localhost:8081 "
                "--deep-rerank --deep-rerank-url http://localhost:8082 --hyde",
                self._server_url,
            )

    def _load_config(self) -> None:
        cfg = _read_config(self._hermes_home)
        self._server_url = cfg.get("server_url", self._server_url)
        # v2.5.1: localhost resolves to ::1 first on dual-stack Python;
        # the server binds IPv4 only. Rewrite to explicit IPv4 — same
        # host, removes the resolver from the failure surface.
        if "//localhost" in self._server_url:
            self._server_url = self._server_url.replace(
                "//localhost", "//127.0.0.1", 1
            )
            logger.info(
                "server_url 'localhost' rewritten to 127.0.0.1 "
                "(IPv6-first resolution workaround)"
            )
        self._auto_capture = bool(cfg.get("auto_capture", self._auto_capture))
        self._auto_recall = bool(cfg.get("auto_recall", self._auto_recall))
        self._max_recall = int(cfg.get("max_recall_memories", self._max_recall))
        self._capture_max_chars = int(
            cfg.get("capture_max_chars", self._capture_max_chars)
        )
        self._briefing_on_start = bool(
            cfg.get("briefing_on_start", self._briefing_on_start)
        )
        if cfg.get("min_similarity") is not None:
            try:
                self._min_similarity = float(cfg["min_similarity"])
            except (TypeError, ValueError):
                self._min_similarity = None
        self._data_dir = cfg.get("data_dir", self._data_dir)

        # v2.8.0 (A3-3): explicit session scope. Only "verification" is
        # meaningful; anything else is ignored so a typo can't invent a
        # scope the server doesn't know.
        # v2.9.0 (RT-13 F2): plugin-side gate. Server observation mode is
        # the safety; this only lets an operator silence the POSTs.
        self._fidelity_enabled = bool(cfg.get("fidelity", self._fidelity_enabled))
        if _fidelity is None and self._fidelity_enabled:
            logger.info("astral-memory: fidelity module absent; RT-13 signals disabled")
            self._fidelity_enabled = False

        scope = str(cfg.get("session_scope", "") or "").strip().lower()
        self._session_scope = scope if scope == "verification" else None
        if self._session_scope:
            logger.info("astral-memory: session_scope=%s (ingest will be tagged)",
                        self._session_scope)

        # §8.1: load default_namespace. Empty string or absent →
        # cross-namespace mode (no namespace sent on writes/reads).
        raw_ns = cfg.get("default_namespace", "")
        if raw_ns and isinstance(raw_ns, str):
            err = _validate_namespace(raw_ns)
            if err:
                # §8.2: invalid config namespace is logged at WARNING
                # and treated as empty. The plugin does not crash.
                logger.warning(
                    "Invalid default_namespace: %s — ignoring", raw_ns,
                )
                self._config_namespace = ""
            else:
                self._config_namespace = raw_ns
        else:
            self._config_namespace = ""

        env_url = os.environ.get("ASTRAL_SERVER_URL")
        if env_url:
            self._server_url = env_url

        # v2.5.0: Bearer auth token. Config key `api_token`; env var
        # ASTRAL_API_TOKEN overrides (parallel to ASTRAL_SERVER_URL).
        self._api_token = str(cfg.get("api_token", "") or "")
        env_token = os.environ.get("ASTRAL_API_TOKEN")
        if env_token:
            self._api_token = env_token

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sid(self, session_id: str = "") -> str:
        """Resolve the effective session id for a call."""
        if session_id:
            return session_id
        with self._state_lock:
            return self._active_session_id

    def _submit(self, fn, *args, **kwargs) -> None:
        """Fire-and-forget background work. Never raises into the turn."""
        if self._pool is None or self._shutting_down:
            return
        try:
            self._pool.submit(fn, *args, **kwargs)
        except RuntimeError:
            # Pool already shut down mid-turn.
            pass

    # ------------------------------------------------------------------
    # RT-13 Layer A — response fidelity (v2.9.0, Amendment A5 §2)
    # ------------------------------------------------------------------

    def _remember_served(self, session_id: str, objs: Any) -> None:
        """Capture id→text for served memories (bounded, non-fatal)."""
        try:
            sid = self._sid(session_id)
            with self._state_lock:
                bucket = self._served_texts.setdefault(sid, {})
                for o in objs or []:
                    if not isinstance(o, dict):
                        continue
                    mid = o.get("id") or o.get("memory_id")
                    if not mid:
                        continue
                    text = o.get("text") or o.get("content") or o.get("summary") or ""
                    bucket[str(mid)] = str(text)[:2000]
                if len(bucket) > 64:
                    for k in list(bucket)[:-64]:
                        bucket.pop(k, None)
        except Exception as e:  # noqa: BLE001
            logger.debug("fidelity: _remember_served skipped: %s", e)

    def _fidelity_post(self, sid: str, turn: int, signals: list) -> None:
        """Build and POST a fidelity batch on the pool. Never raises."""
        if not signals or _fidelity is None or not self._ready():
            return
        payload = _fidelity.build_payload(
            sid, turn, signals, source=f"hermes-{_VERSION}",
            cosine_available=False,
        )
        if not payload:
            return

        def _send() -> None:
            r = self._http.post("/v1/memories/fidelity", payload)
            if "error" in r:
                logger.debug("fidelity POST failed (non-fatal): %s", r["error"])
            else:
                logger.debug("fidelity: applied=%s logged=%s obs=%s rejected=%s",
                             r.get("applied"), r.get("logged"),
                             r.get("observation_mode"), len(r.get("rejected") or []))

        self._submit(_send)

    def _fidelity_after_turn(self, sid: str, user_content: str,
                             assistant_content: str) -> None:
        """A1 (evidence-use) for the turn just completed + stash A2 context."""
        if not self._fidelity_enabled or _fidelity is None:
            return
        try:
            with self._state_lock:
                served_ids = list(self._recalled_ids.get(sid, []))
                texts = dict(self._served_texts.get(sid, {}))
                turn = int(self._turn_counts.get(sid, 0))
                # Explicit astral_recall results are served too, even if
                # the server's augmented-prompt didn't list them.
                for mid in texts:
                    if mid not in served_ids:
                        served_ids.append(mid)
                served_ids = served_ids[:32]
                self._fidelity_pending[sid] = {
                    "turn": turn,
                    "served": served_ids,
                    "prev_user": (user_content or "")[:2000],
                }
            if not served_ids:
                return
            served = [_fidelity.ServedMemory(i, texts.get(i, "")) for i in served_ids]
            a1 = _fidelity.a1_evidence_use(served, assistant_content or "")
            logger.debug("fidelity A1 turn=%s served=%d verdicts=%s",
                         turn, len(served_ids), a1.per_memory)
            self._fidelity_post(sid, turn, a1.signals)
        except Exception as e:  # noqa: BLE001 — never let feedback kill a turn
            logger.debug("fidelity A1 skipped: %s", e)

    def _fidelity_next_turn(self, sid: str, message: str) -> None:
        """A2 (continuation/correction) for the previous turn's served ids."""
        if not self._fidelity_enabled or _fidelity is None:
            return
        try:
            with self._state_lock:
                pending = self._fidelity_pending.pop(sid, None)
            if not pending or not pending.get("served"):
                return
            a2 = _fidelity.a2_next_turn(pending["served"], message or "",
                                        pending.get("prev_user", ""))
            logger.debug("fidelity A2 turn=%s verdict=%s reason=%s",
                         pending["turn"], a2.verdict, a2.reason)
            self._fidelity_post(sid, int(pending["turn"]), a2.signals)
        except Exception as e:  # noqa: BLE001
            logger.debug("fidelity A2 skipped: %s", e)

    def _ready(self) -> bool:
        return self._http is not None

    def _resolve_namespace(self) -> str:
        """Return the active namespace, or empty string for cross-namespace.

        Resolution order (§7.2):
          1. Session-level override (_active_namespace, set via astral_namespace)
          2. Config default (_config_namespace, from default_namespace)
          3. Empty (no namespace sent — server defaults on write, searches
             all on read)

        When the server does not support namespace (§14.1), returns empty
        string so all namespace params are silently omitted.
        """
        if not self._server_supports_namespace:
            return ""
        with self._state_lock:
            return self._active_namespace or self._config_namespace

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            schemas.ASTRAL_RECALL,
            schemas.ASTRAL_STORE,
            schemas.ASTRAL_FORGET,
            schemas.ASTRAL_BRIEFING,
            schemas.ASTRAL_DIARY,
            schemas.ASTRAL_STATS,
            schemas.ASTRAL_SYNC,
            schemas.ASTRAL_NAMESPACE,
            schemas.ASTRAL_PREFERENCES,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any],
                         **kwargs) -> str:
        """Dispatch a tool call. Must return a JSON string.

        `kwargs` may carry session_id and other context in future Hermes
        releases; accept and ignore what we don't use.
        """
        if not self._ready():
            return json.dumps({"error": "Astral Core provider not initialized"})

        session_id = self._sid(kwargs.get("session_id", ""))

        dispatch = {
            "astral_recall":   self._tool_recall,
            "astral_store":    self._tool_store,
            "astral_forget":   self._tool_forget,
            "astral_briefing": self._tool_briefing,
            "astral_diary":    self._tool_diary,
            "astral_stats":    self._tool_stats,
            "astral_sync":     self._tool_sync,
            "astral_namespace": self._tool_namespace,
            "astral_preferences": self._tool_preferences,
        }

        handler = dispatch.get(tool_name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            return handler(args, session_id)
        except Exception as e:  # noqa: BLE001
            logger.error("Tool %s failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    def _tool_recall(self, args: dict, session_id: str) -> str:
        payload: Dict[str, Any] = {
            "query": args.get("query", ""),
            "limit": args.get("limit", self._max_recall),
        }
        if self._min_similarity is not None:
            payload["min_similarity"] = self._min_similarity

        # §11.2: per-call namespace override.
        # "all" is a special token forcing cross-namespace search.
        ns_arg = args.get("namespace", "")
        if ns_arg == "all":
            pass  # cross-namespace — don't send namespace_filter
        elif ns_arg:
            payload["namespace_filter"] = ns_arg
        else:
            ns = self._resolve_namespace()
            if ns:
                payload["namespace_filter"] = ns

        data = self._http.post("/v1/memory/search", payload)
        # v2.8.0 (P-2): render provenance so the agent can weigh what it
        # recalls. Server emits source_role/content_class/user_intent as
        # top-level fields (R-5) and in metadata; legacy memories have none.
        try:
            results = data.get("results") if isinstance(data, dict) else None
            if isinstance(results, list):
                tagged = 0
                for r in results:
                    tag = _provenance_tag(r)
                    if tag:
                        r["provenance"] = tag
                        tagged += 1
                if tagged:
                    data["provenance_legend"] = _PROVENANCE_LEGEND
                # v2.9.0 (RT-13 F2): remember id→text so A1 can score
                # evidence-use when this turn is synced.
                self._remember_served(session_id, results)
        except Exception as e:  # never let rendering break recall
            logger.debug("provenance rendering skipped: %s", e)
        return json.dumps(data, indent=2, default=str)

    def _tool_store(self, args: dict, session_id: str) -> str:
        """Store the caller's text verbatim via the direct /v1/memory/add path.

        v2.9.1 (STORE-1): explicit stores must NOT go through
        /v1/memory/ingest — that endpoint feeds the dyad extractor, which
        rewrites the input into derived observations and drops the
        original wording. /v1/memory/add persists the text as given, with
        no surprise gate, and returns the new memory id.
        """
        text = (args.get("text") or "").strip()
        if not text:
            return json.dumps({"error": "text is required"})
        category = (args.get("category") or "fact").strip() or "fact"
        source = f"hermes_explicit_{session_id}" if session_id \
                 else "hermes_explicit"

        payload: Dict[str, Any] = {
            "text": text,
            "category": category,
            "source": source,
            "metadata": {
                "source": "hermes_explicit",
                "category": category,
            },
        }

        # §10.2: per-call namespace override, else resolve from session.
        # Key is omitted entirely when no namespace is active so the server
        # applies its own default (contract: test_store_omits_namespace_*).
        ns = args.get("namespace", "") or self._resolve_namespace()
        if ns:
            payload["namespace"] = ns

        result = self._http.post("/v1/memory/add", payload)
        if not isinstance(result, dict):
            return json.dumps({"error": f"unexpected server reply: {result!r}"})
        if "error" in result:
            return json.dumps({"error": result["error"]})

        return json.dumps({
            "success": bool(result.get("stored", True)),
            "id": result.get("memory_id", result.get("id", "")),
            "text": text,
            "category": category,
            "namespace": ns or None,
            "total_memories": result.get("total_memories"),
        }, indent=2, default=str)

    def _tool_forget(self, args: dict, session_id: str) -> str:
        source = args.get("source", "")
        if not source:
            return json.dumps({"error": "source is required"})
        return json.dumps(
            self._http.delete(f"/v1/memory/source/{source}"),
            indent=2, default=str,
        )

    def _tool_briefing(self, args: dict, session_id: str) -> str:
        return json.dumps(
            self._http.get("/v1/memory/briefing",
                           params={"max_tokens": args.get("max_tokens", 200)}),
            indent=2, default=str,
        )

    def _tool_diary(self, args: dict, session_id: str) -> str:
        action = args.get("action", "read")
        if action == "write":
            text = args.get("text", "")
            if not text:
                return json.dumps({"error": "text is required for diary write"})
            result = self._http.post("/v1/diary/write", {
                "entry_text": text,
                "agent_id": "hermes",
                "entry_type": args.get("entry_type", "note"),
                "session_id": session_id,
            })
        else:
            result = self._http.get("/v1/diary/read", params={
                "limit": args.get("limit", 10),
                "agent_id": "hermes",
            })
        return json.dumps(result, indent=2, default=str)

    def _tool_stats(self, args: dict, session_id: str) -> str:
        return json.dumps(self._http.get("/v1/memory/stats"),
                          indent=2, default=str)

    def _tool_sync(self, args: dict, session_id: str) -> str:
        return json.dumps(self._http.post("/v1/sync/trigger"),
                          indent=2, default=str)

    def _tool_namespace(self, args: dict, session_id: str) -> str:
        """Set, get, or clear the active memory namespace (§12).

        This is the keystone of the namespace feature — without it, the
        agent has no way to set namespace mid-conversation.
        """
        action = args.get("action", "get")

        if action == "get":
            ns = self._resolve_namespace()
            return json.dumps({
                "active_namespace": ns or None,
                "config_default": self._config_namespace or None,
                "mode": "namespace" if ns else "cross-namespace",
                "server_supported": self._server_supports_namespace,
            }, indent=2)

        if action == "set":
            ns = args.get("namespace", "").strip()
            if not ns:
                return json.dumps(
                    {"error": "namespace is required for 'set'"}
                )
            err = _validate_namespace(ns)
            if err:
                return json.dumps({"error": err})
            with self._state_lock:
                self._active_namespace = ns
            logger.info(
                "Namespace set to '%s' for session %s", ns, session_id,
            )
            return json.dumps({
                "active_namespace": ns,
                "mode": "namespace",
            }, indent=2)

        if action == "clear":
            with self._state_lock:
                self._active_namespace = ""
            logger.info("Namespace cleared for session %s", session_id)
            return json.dumps({
                "active_namespace": None,
                "mode": "cross-namespace",
            }, indent=2)

        return json.dumps({"error": f"Unknown action: {action}"})

    # ------------------------------------------------------------------
    # Preferences — consent surface (SPEC-PREFERENCE-DISTILLATION-001)
    # ------------------------------------------------------------------

    def _tool_preferences(self, args: dict, session_id: str) -> str:
        """Manage learned user preferences (§6 consent flow)."""
        action = args.get("action", "pending")

        if action == "pending":
            return self._pref_pending()
        elif action == "approve":
            return self._pref_transition(args, "active")
        elif action == "decline":
            return self._pref_transition(args, "declined")
        elif action == "withdraw":
            return self._pref_transition(args, "withdrawn")
        elif action == "declare":
            return self._pref_declare(args)
        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    def _pref_pending(self) -> str:
        """List pending preference nominations with evidence."""
        data = self._http.post("/v1/memory/list", {
            "namespace": "preferences",
            "limit": 50,
        })
        if "error" in data:
            return json.dumps({"error": data["error"]})

        pending = []
        for m in data.get("memories", []):
            status = (m.get("metadata") or {}).get("status", "")
            if status != "pending":
                continue
            # Fetch evidence snippets from supporting memory IDs
            support_ids = (m.get("metadata") or {}).get(
                "supporting_memory_ids", []
            )
            snippets = []
            for sid in support_ids[:3]:  # EVIDENCE_SNIPPET_COUNT = 3
                mem_data = self._http.get(f"/v1/memory/{sid}")
                if "error" not in mem_data and "memory" in mem_data:
                    mem = mem_data["memory"]
                    snippets.append({
                        "id": sid,
                        "text": mem.get("text", "")[:200],
                        "created_at": mem.get("created_at", ""),
                    })
            pending.append({
                "id": m["id"],
                "proposed_text": m["text"],
                "category": m.get("category", ""),
                "evidence_sessions": (m.get("metadata") or {}).get(
                    "evidence_sessions", 0
                ),
                "evidence_span_days": (m.get("metadata") or {}).get(
                    "evidence_span_days", 0
                ),
                "evidence_snippets": snippets,
            })

        return json.dumps({
            "pending_count": len(pending),
            "nominations": pending,
            "hint": (
                "Present each nomination to the user with its evidence. "
                "Ask: 'Should I remember this as a standing preference?' "
                "Use approve/decline with the preference_id."
            ) if pending else "No pending nominations.",
        }, indent=2)

    def _pref_transition(self, args: dict, new_status: str) -> str:
        """Approve, decline, or withdraw a preference via PATCH."""
        pref_id = args.get("preference_id", "").strip()
        if not pref_id:
            return json.dumps({"error": "preference_id is required"})

        result = self._http.request(
            "PATCH",
            f"/v1/memory/{pref_id}",
            payload={"metadata": {"status": new_status}},
        )
        if "error" in result:
            return json.dumps({"error": result["error"]})

        return json.dumps({
            "success": True,
            "id": pref_id,
            "new_status": new_status,
            "changed": result.get("changed", []),
        }, indent=2)

    def _pref_declare(self, args: dict) -> str:
        """Declare a user preference directly (bypasses H7 discovery)."""
        text = (args.get("text") or "").strip()
        if not text:
            return json.dumps({"error": "text is required for declare"})
        if len(text) > 140:
            return json.dumps({
                "error": f"Preference text too long ({len(text)} chars, max 140)"
            })

        result = self._http.post("/v1/memory/add", {
            "text": text,
            "namespace": "preferences",
            "source": "user_declared",
            "metadata": {
                "status": "active",  # consent inherent in declaration
                "source": "user_declared",
                "category": "preference",
            },
        })
        if "error" in result:
            return json.dumps({"error": result["error"]})

        return json.dumps({
            "success": True,
            "id": result.get("memory_id", result.get("id", "")),
            "text": text,
            "status": "active",
            "note": "Preference declared and immediately active.",
        }, indent=2)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._server_healthy:
            return ""
        # v2.7.0: mention preferences if any were injected last turn.
        # Uses the cached flag from _do_prefetch — no HTTP call here.
        # The injection block itself makes the preferences visible;
        # this note tells the agent the capability is live.
        pref_note = ""
        with self._state_lock:
            if getattr(self, "_last_pref_count", 0) > 0:
                pref_note = " Preferences active (injected below)."

        return (
            "[Astral Core Memory active — surprise-gated, offline, "
            f"{self._max_recall} memories per turn."
            f"{pref_note} "
            "Use astral_recall before answering from past context.]"
        )

    # ------------------------------------------------------------------
    # Recall — prefetch / queue_prefetch
    # ------------------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall context for the upcoming turn. Fast: prefer the cache."""
        if not self._auto_recall or not self._ready():
            return ""

        sid = self._sid(session_id)

        with self._state_lock:
            cached = self._prefetch_cache.pop(sid, None)
        if cached:
            return cached

        # Synchronous fallback — first turn of a session, or the background
        # warm hasn't landed yet.
        return self._do_prefetch(query, sid)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Warm the cache for the next turn. Non-blocking.

        MemoryManager already dispatches this on a background worker, so we
        do the work inline rather than spawning a second thread.
        """
        if not self._auto_recall or not self._ready():
            return
        sid = self._sid(session_id)
        result = self._do_prefetch(query, sid)
        if result:
            with self._state_lock:
                self._prefetch_cache[sid] = result

    def _do_prefetch(self, query: str, session_id: str) -> str:
        if not query.strip():
            return ""
        payload: Dict[str, Any] = {
            "query": query,
            "max_memories": self._max_recall,
        }
        # v2.7.2 (R-ECHO-1b): tell the server whose session is asking so
        # its same-session echo filter can exclude THIS session's own
        # turns from the injected context (N-11: the question was echoing
        # itself at 0.89). Same sid derivation as sync_turn ingest.
        if session_id:
            payload["session_id"] = session_id
        if self._min_similarity is not None:
            payload["min_similarity"] = self._min_similarity

        # §11.1: auto-recall respects the active namespace.
        ns = self._resolve_namespace()
        if ns:
            payload["namespace_filter"] = ns
            logger.debug("prefetch: filtering by namespace '%s'", ns)

        data = self._http.post("/v1/memory/augmented-prompt", payload)
        if "error" in data:
            logger.debug("Prefetch failed (non-fatal): %s", data["error"])
            return ""

        block = str(data.get("context_block", "") or "")

        # v2.5.0 / NS-1: the server flags when the requested namespace had
        # zero results and it fell back to cross-namespace. Label the block
        # so the agent knows these memories are from outside the active
        # namespace. Graceful on older servers (key absent → no label).
        if block and data.get("namespace_fallback") is True:
            ns = self._resolve_namespace()
            block = (
                f"[note: no memories found in namespace '{ns}' — "
                f"the following are cross-namespace results]\n" + block
            )

        # Track which memories the server injected (server >= v2.12.x
        # returns `memory_ids`; older servers omit it — treat as empty,
        # never let provenance tracking break recall).
        ids = data.get("memory_ids")
        if isinstance(ids, list):
            with self._state_lock:
                self._recalled_ids[session_id] = [
                    str(i) for i in ids if i
                ][:32]
        # v2.9.0 (RT-13 F2): if the server also returns per-memory objects
        # (any of memories/results/items with id+text), keep the text for
        # A1. Older servers return ids only → those stay UNSCORED for A1.
        for key in ("memories", "results", "items"):
            objs = data.get(key)
            if isinstance(objs, list) and objs:
                self._remember_served(session_id, objs)
                break

        # Optional: prepend the briefing card on the first recall of a session.
        if self._briefing_on_start:
            with self._state_lock:
                first = self._turn_counts.get(session_id, 0) <= 1
            if first:
                card = self._http.get("/v1/memory/briefing",
                                      params={"max_tokens": 200})
                text = str(card.get("briefing", "") or "") if "error" not in card else ""
                if text:
                    block = f"{text}\n\n{block}".strip()

        # ── SPEC-PREFERENCE-DISTILLATION-001 §5: always-on injection ────
        # Second fetch: all active preferences from the preferences
        # namespace. Total injection (no rotation) — the §4 cap (12
        # active, 1500 chars) guarantees this stays smaller than one
        # tool result. Filtered client-side by status == active.
        #
        # §5 self-sustaining loop: the server's list_memories handler
        # fires record_hit for every memory returned when the namespace
        # is "preferences" (R3 fix, server campaign). This means every
        # injection-fetch accumulates spaced access on active preferences,
        # keeping them SLOW via genuine usage — the lifecycle IS the
        # retirement mechanism. If the server ever changes list_memories
        # to NOT record hits, this loop breaks silently — the §9 liveness
        # invariant (preferences_active declining unexpectedly) is the
        # detection signal.
        pref_block = ""
        pref_data = self._http.post("/v1/memory/list", {
            "namespace": "preferences",
            "limit": 50,
        })
        if "error" not in pref_data:
            active_prefs = []
            active_ids = []
            for m in pref_data.get("memories", []):
                status = (m.get("metadata") or {}).get("status", "")
                if status == "active":
                    # §5.1: canonical injected form — one sentence, ≤140 chars
                    canonical = m.get("text", "")[:140].strip()
                    if canonical:
                        active_prefs.append(f"- {canonical}")
                        active_ids.append(m.get("id", "?"))

            if active_prefs:
                pref_block = (
                    "\n[USER PREFERENCES — learned, consented]\n"
                    + "\n".join(active_prefs)
                    + "\n[/USER PREFERENCES]"
                )

            # §5.2: injection trace — per-turn DEBUG log
            total_chars = sum(len(p) for p in active_prefs)
            logger.debug(
                "Preference injection: active=%d injected=%d chars=%d ids=%s",
                len(active_prefs), len(active_prefs), total_chars,
                ",".join(active_ids) if active_ids else "none",
            )

            # Cache the count for system_prompt_block (no HTTP call there).
            with self._state_lock:
                self._last_pref_count = len(active_prefs)

        if not pref_block:
            with self._state_lock:
                self._last_pref_count = 0

        if pref_block:
            block = block + pref_block if block else pref_block.strip()

        return block

    # ------------------------------------------------------------------
    # Capture — sync_turn
    # ------------------------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Persist a completed turn. Must be non-blocking."""
        if not self._auto_capture or not self._ready():
            return

        # v2.7.1 (N-10a fix): the v2.3.0 8-word pre-filter is removed. It was
        # added to shield Dreamer episode generation from noise, but short
        # turns are often the highest-signal ones (proper nouns, answers,
        # confirmations — "Mina the Hollower" is 3 words). Under
        # SPEC-EPISODE-BOUNDARY-001 filtering is the server's job
        # (SEC-ORD-1: redact → spool-append → noise → gate); a client-side
        # word count punches holes in the raw-turn spool's completeness
        # guarantee. Only genuinely empty turns are skipped.
        combined = f"{user_content} {assistant_content}"
        if not combined.strip():
            logger.debug("sync_turn: skipped empty turn")
            return

        sid = self._sid(session_id)

        # §10.1: resolve namespace for auto-capture.
        ns = self._resolve_namespace()

        # v2.9.0 (RT-13 F2): fidelity Layer A, same turn. Snapshot the
        # served ids NOW (prefetch for the next turn will overwrite them),
        # score A1 against the assistant text, and stash the A2 context.
        self._fidelity_after_turn(sid, user_content, assistant_content)

        def _sync() -> None:
            payload: Dict[str, Any] = {
                "user_message": user_content[:self._capture_max_chars],
                "assistant_response": assistant_content[:self._capture_max_chars],
                "source": f"hermes_{sid}" if sid else "hermes",
            }
            # v2.7.2 (R-ECHO-1b): stamp the session id explicitly so stored
            # memories carry metadata.session_id — the server-side
            # same-session echo filter needs it. Same sid as `source`, so
            # ingest and prefetch can never drift apart.
            if sid:
                payload["session_id"] = sid
            if ns:
                payload["namespace"] = ns
                logger.debug("sync_turn: writing to namespace '%s'", ns)
            # v2.8.0 (A3-3): explicit, client-set verification scope.
            if self._session_scope:
                payload["session_scope"] = self._session_scope
            result = self._http.post("/v1/memory/ingest", payload)
            if "error" in result:
                logger.debug("sync_turn failed (non-fatal): %s", result["error"])

        self._submit(_sync)

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        sid = self._sid(kwargs.get("session_id", ""))
        with self._state_lock:
            self._turn_counts[sid] = turn_number

        # v2.9.0 (RT-13 F2): fidelity Layer A2 — the user's next message
        # tells us whether the previous response landed.
        self._fidelity_next_turn(sid, message)

        if (_CONSOLIDATE_EVERY_N_TURNS
                and turn_number > 0
                and turn_number % _CONSOLIDATE_EVERY_N_TURNS == 0
                and self._ready()):
            self._submit(self._http.post, "/v1/memory/consolidate")

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Reassign per-session state.

        Without this, `_active_session_id` stayed frozen at whatever
        initialize() saw, and every subsequent write was tagged with a stale
        source id after /new, /reset, /branch, /resume, or compression.
        """
        with self._state_lock:
            old = self._active_session_id
            self._active_session_id = new_session_id

            # §9.2: reset active namespace to config default on session
            # switch. Multi-session contexts each get their own namespace
            # state via session_id keying.
            self._active_namespace = self._config_namespace
            logger.debug(
                "Session changed: namespace reset to '%s'",
                self._config_namespace or "<none>",
            )

            if reset:
                # Genuinely new conversation — drop everything.
                self._prefetch_cache.clear()
                self._turn_counts.clear()
                self._recalled_ids.clear()
            else:
                # Continuation: carry the turn count forward, but never carry
                # recalled context across an id boundary.
                self._prefetch_cache.pop(old, None)
                self._prefetch_cache.pop(new_session_id, None)
                self._recalled_ids.pop(old, None)
                self._recalled_ids.pop(new_session_id, None)
                if old and old in self._turn_counts:
                    self._turn_counts[new_session_id] = self._turn_counts.pop(old)

            if rewound:
                self._prefetch_cache.pop(new_session_id, None)

        logger.debug(
            "Session switch %s -> %s (reset=%s rewound=%s parent=%s)",
            old or "<none>", new_session_id, reset, rewound,
            parent_session_id or "<none>",
        )

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Capture turns about to be discarded, and contribute to the summary.

        Returns text the compressor folds into its summarization prompt.
        """
        if not self._ready() or not messages:
            return ""

        sid = self._sid()

        pairs: List[Dict[str, str]] = []
        for i in range(len(messages) - 1):
            if (messages[i].get("role") == "user"
                    and messages[i + 1].get("role") == "assistant"):
                user_text = str(messages[i].get("content", "") or "").strip()
                asst_text = str(messages[i + 1].get("content", "") or "").strip()
                # v2.7.1 (N-10b fix): the `len(user_text) > 20` char gate is
                # removed for the same reason as the sync_turn word filter —
                # short user turns carry high-signal proper nouns and must
                # reach the server-side pipeline. Non-empty is sufficient.
                if user_text and asst_text:
                    pairs.append({
                        "user": user_text[:self._capture_max_chars],
                        "assistant": asst_text[:self._capture_max_chars],
                    })

        if not pairs:
            return ""

        batch = pairs[:10]

        # §10.3: pre-compress capture inherits the session-level namespace.
        ns = self._resolve_namespace()

        def _ingest() -> None:
            payload: Dict[str, Any] = {
                "turns": batch,
                "source": f"hermes_compress_{sid}" if sid else "hermes_compress",
            }
            if ns:
                payload["namespace"] = ns
            result = self._http.post("/v1/memory/ingest/batch", payload)
            if "error" in result:
                logger.debug("Pre-compress capture failed: %s", result["error"])
            else:
                logger.debug("Pre-compress capture: %d pairs", len(batch))

        self._submit(_ingest)

        return (
            f"[Astral Core] {len(batch)} conversation turn(s) from this span "
            "have been persisted to long-term memory and remain retrievable "
            "via astral_recall. The summary need not preserve their verbatim "
            "detail — preserve decisions, constraints, and open threads."
        )

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Generate the session episode, notify the Dreamer, write the diary.

        SPEC-PLUGIN-SESSION-END-002 v1.0.0. Order matters and everything
        is synchronous — the pool may be torn down right after this hook:
          1. /v1/episodes/generate with the explicit transcript (>=3
             turns) — the reliable episode path. The Dreamer's ID-based
             collection finds zero turns (server-internal session_id
             never matches the Hermes UUID; see SRV-2 in header).
          2. /v1/memory/session-end — Dreamer event, retained for
             timeline markers and HOPE signals.
          3. Diary summary (unchanged).
        """
        if not self._ready() or not messages:
            return

        sid = self._sid()

        # ── 1. Extract transcript turns ─────────────────────────────────
        turns: List[Dict[str, str]] = []
        for msg in messages:
            role = msg.get("role", "")
            content = str(msg.get("content", "") or "").strip()
            if content and role in ("user", "assistant"):
                turns.append({
                    "text": content[: self._capture_max_chars],
                    "speaker": role,  # server EpisodeTurn: text + speaker
                })

        # ── 2. Direct episode generation (reliable path) ────────────────
        if len(turns) >= 3:
            ep = self._http.post("/v1/episodes/generate", {
                "session_id": sid or "",
                "turns": turns,
            })
            if "error" in ep:
                logger.warning(
                    "Episode generation failed for session %s: %s",
                    sid, ep["error"],
                )
            else:
                logger.info(
                    "Episode generated for session %s: stored=%s backend=%s "
                    "turns=%d",
                    sid, ep.get("stored"), ep.get("llm_backend_used"),
                    len(turns),
                )
        else:
            logger.debug(
                "Session %s ended with %d turns (<3) — episode skipped",
                sid, len(turns),
            )

        # ── 3. Dreamer event (timeline markers / HOPE — not episode text) ─
        end_result = self._http.post("/v1/memory/session-end", {
            "session_id": sid or "",
        })
        if "error" in end_result:
            logger.warning(
                "session-end notification failed for session %s: %s",
                sid, end_result["error"],
            )
        else:
            logger.debug("SessionCompleted sent for session %s", sid)

        lines: List[str] = []
        for msg in messages[-6:]:
            role = msg.get("role", "")
            content = str(msg.get("content", "") or "").strip()[:500]
            if content and role in ("user", "assistant"):
                lines.append(f"[{'U' if role == 'user' else 'A'}] {content}")

        if not lines:
            return

        # Synchronous: the pool may be torn down immediately after this.
        result = self._http.post("/v1/diary/write", {
            "entry_text": "\n".join(lines[-4:]),
            "agent_id": "hermes",
            "session_id": sid,
            "entry_type": "summary",
            "metadata": {"auto": True, "plugin": "astral-memory",
                         "version": _VERSION},
        })
        if "error" in result:
            logger.debug("Diary write failed: %s", result["error"])

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """Capture what a subagent was asked and what it returned.

        Subagents run with skip_memory=True — the parent provider is the only
        place this can land. In <= 2.1.0 it was discarded entirely.
        """
        if not self._ready() or not task.strip() or not result.strip():
            return

        sid = self._sid(kwargs.get("session_id", ""))

        # v2.3.0: attach the parent's recalled memory IDs as provenance.
        # The sub-agent ran with skip_memory=True and never saw these; the
        # manifest records which parent context *surrounded* the delegation
        # so the consolidated result is linked to it in the corpus.
        with self._state_lock:
            manifest = list(self._recalled_ids.get(sid, ()))

        # §9.3: subagents inherit the parent's namespace.
        ns = self._resolve_namespace()

        def _ingest() -> None:
            payload: Dict[str, Any] = {
                "user_message": task[:self._capture_max_chars],
                "assistant_response": result[:self._capture_max_chars],
                "source": f"hermes_delegation_{child_session_id or sid}",
            }
            if ns:
                payload["namespace"] = ns
            if manifest:
                payload["metadata"] = {
                    "delegation": True,
                    "context_manifest": manifest,
                    "child_session_id": child_session_id or None,
                }
            self._http.post("/v1/memory/ingest", payload)

        self._submit(_ingest)

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in MEMORY.md / USER.md writes into Astral Core."""
        if not self._ready() or not content.strip():
            return
        if action not in ("add", "replace"):
            return

        meta = dict(metadata or {})
        sid = self._sid(str(meta.get("session_id", "")))

        # Namespace: MEMORY.md/USER.md writes inherit the active namespace.
        ns = self._resolve_namespace()

        def _mirror() -> None:
            payload: Dict[str, Any] = {
                "user_message": content[:self._capture_max_chars],
                "assistant_response": f"Mirrored from Hermes {target} ({action}).",
                "source": f"hermes_builtin_{target}",
                "metadata": {
                    "mirrored": True,
                    "session_id": sid,
                    "write_origin": meta.get("write_origin"),
                    "tool_name": meta.get("tool_name"),
                },
            }
            if ns:
                payload["namespace"] = ns
            self._http.post("/v1/memory/ingest", payload)

        self._submit(_mirror)

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def backup_paths(self) -> List[str]:
        """Declare the Astral corpus so `hermes backup` captures it.

        Contract: must work without initialize() and without network.
        """
        cfg = _read_config(_default_hermes_home())
        data_dir = os.environ.get("ASTRAL_DATA_DIR") or cfg.get(
            "data_dir", _DEFAULT_DATA_DIR
        )
        return [str(Path(data_dir).expanduser())]

    # ------------------------------------------------------------------
    # Config schema (`hermes memory setup`)
    # ------------------------------------------------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "server_url",
                "description": "Astral Core memory server URL",
                "default": _DEFAULT_SERVER_URL,
            },
            {
                "key": "data_dir",
                "description": "Astral Core data directory (backed up by `hermes backup`)",
                "default": _DEFAULT_DATA_DIR,
            },
            {
                "key": "auto_capture",
                "description": "Automatically capture conversation turns",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "auto_recall",
                "description": "Automatically recall memories before each turn",
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "max_recall_memories",
                "description": "Maximum memories to inject per turn",
                "default": "8",
            },
            {
                "key": "session_scope",
                "description": (
                    "Optional. Set to 'verification' for a Hermes instance used "
                    "to run fixtures/canaries; ingested memories are tagged so "
                    "the server can down-rank them (A3-3). Leave empty otherwise."
                ),
                "default": "",
            },
            {
                "key": "fidelity",
                "description": (
                    "Send RT-13 response-fidelity signals (ids only, no text) "
                    "to the server after each turn. The server decides whether "
                    "to apply or only log them (observation mode)."
                ),
                "default": "true",
                "choices": ["true", "false"],
            },
            {
                "key": "briefing_on_start",
                "description": "Prepend the briefing card on the first turn of a session",
                "default": "false",
                "choices": ["true", "false"],
            },
            {
                "key": "default_namespace",
                "description": (
                    "Default memory namespace for this device. Empty = "
                    "cross-namespace (search all, write to server default). "
                    "Lowercase alphanumeric + hyphens, max 64 chars."
                ),
                "default": "",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Merge into astral-memory.json rather than clobbering it."""
        path = _config_path(hermes_home)
        existing = _read_config(hermes_home)

        coerced = dict(values)
        for key in ("auto_capture", "auto_recall", "briefing_on_start"):
            if key in coerced and isinstance(coerced[key], str):
                coerced[key] = coerced[key].strip().lower() in ("true", "1", "yes")

        if "max_recall_memories" in coerced:
            try:
                coerced["max_recall_memories"] = int(coerced["max_recall_memories"])
            except (TypeError, ValueError):
                coerced["max_recall_memories"] = 8

        if "default_namespace" in coerced:
            ns = str(coerced["default_namespace"] or "").strip()
            if ns:
                err = _validate_namespace(ns)
                if err:
                    logger.warning("Invalid default_namespace in save_config: %s", ns)
                    ns = ""
            coerced["default_namespace"] = ns

        existing.update(coerced)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2))
        logger.info("Config saved to %s", path)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        self._shutting_down = True
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=False)
            self._pool = None
        logger.debug("Astral Core Memory provider shut down")


# ---------------------------------------------------------------------------
# Contract self-check
# ---------------------------------------------------------------------------

# (method_name, required parameter names beyond `self`)
_CONTRACT: Dict[str, tuple] = {
    "prefetch":        ("query", "session_id"),
    "queue_prefetch":  ("query", "session_id"),
    "sync_turn":       ("user_content", "assistant_content", "session_id"),
    "handle_tool_call": ("tool_name", "args"),
    "on_pre_compress": ("messages",),
    "on_session_switch": ("new_session_id",),
}


def _assert_contract(provider: MemoryProvider) -> bool:
    """Compare our overrides against the live ABC.

    v2.1.0 shipped with `prefetch(self, query)` against an ABC that had moved
    to `prefetch(self, query, *, session_id="")`. MemoryManager swallowed the
    resulting TypeError at DEBUG level and memory silently stopped working.
    This check turns the next such drift into a visible startup error.
    """
    ok = True
    for method, required in _CONTRACT.items():
        impl = getattr(provider, method, None)
        base = getattr(MemoryProvider, method, None)
        if impl is None or base is None:
            logger.error(
                "astral-memory: method '%s' missing from provider or from the "
                "installed Hermes MemoryProvider ABC. Upgrade Hermes to "
                ">= v2026.7.1 or pin astral-memory-hermes==2.1.0.", method,
            )
            ok = False
            continue

        try:
            sig = inspect.signature(impl)
        except (TypeError, ValueError):
            continue

        params = sig.parameters
        accepts_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        for param in required:
            if param not in params and not accepts_kwargs:
                logger.error(
                    "astral-memory: %s() does not accept '%s'. The installed "
                    "Hermes will call it that way and the failure will be "
                    "silent. This plugin build is incompatible.",
                    method, param,
                )
                ok = False

    if ok:
        logger.debug("astral-memory: MemoryProvider contract check passed")
    return ok


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the Astral Core memory provider with Hermes."""
    provider = AstralCoreMemoryProvider()
    _assert_contract(provider)
    ctx.register_memory_provider(provider)
