# Generation Timestamp: 2026-10-13T20:40:00Z
# Purpose: memory-hermes v2.12.0 contract tests — live-turn prefetch
#          (prefetch_mode), inject floor, T0 vacuity pre-filter, astral_forget
#          by id / query, turn-scoped RT-13 served set, distillation engine
#          construction against astral_distillation v2.0.0, config load/save.
#
# tests/test_2_12_0.py
#
# Run from the repo root like the rest of the suite:
#   ASTRAL_CONTRACT_REQUIRE=1 PYTHONPATH=/srv/oc-projects/hermes/hermes-agent \
#       python3 -m pytest tests/test_2_12_0.py -q
#
# With ASTRAL_CONTRACT_REQUIRE unset and no Hermes on the path, a minimal
# MemoryProvider stub is installed so the plugin imports; the contract
# signatures are then checked against the plugin's own _CONTRACT table only.
#
# No network: every test drives the provider through a FakeHttp that records
# calls and answers with the server shapes documented in the plugin header
# (augmented-prompt with context_block + memory_ids + F11 `memories`,
# /v1/memory/search results, DELETE /v1/memory/{id}).

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PLUGIN_DIR = _REPO / "astral-memory"


# ---------------------------------------------------------------------------
# Import plumbing
# ---------------------------------------------------------------------------

def _ensure_hermes_abc() -> None:
    """Import the real ABC when required/available, else install a stub."""
    try:
        importlib.import_module("agent.memory_provider")
        return
    except ImportError:
        if os.environ.get("ASTRAL_CONTRACT_REQUIRE") == "1":
            raise
    agent = types.ModuleType("agent")
    mp = types.ModuleType("agent.memory_provider")

    class MemoryProvider:  # minimal stand-in, same keyword contract
        def prefetch(self, query, *, session_id=""): ...
        def queue_prefetch(self, query, *, session_id=""): ...
        def sync_turn(self, user_content, assistant_content, *,
                      session_id="", messages=None): ...
        def handle_tool_call(self, tool_name, args, **kwargs): ...
        def on_pre_compress(self, messages): ...
        def on_session_switch(self, new_session_id, **kwargs): ...

    mp.MemoryProvider = MemoryProvider
    agent.memory_provider = mp
    sys.modules["agent"] = agent
    sys.modules["agent.memory_provider"] = mp


def _load_plugin():
    """Load astral-memory/ as package `astral_memory` (hyphen dir is not
    importable by name; this mirrors the installed layout)."""
    _ensure_hermes_abc()
    if "astral_memory" in sys.modules:
        return sys.modules["astral_memory"]
    spec = importlib.util.spec_from_file_location(
        "astral_memory", _PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(_PLUGIN_DIR)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["astral_memory"] = mod
    spec.loader.exec_module(mod)
    return mod


am = _load_plugin()


# ---------------------------------------------------------------------------
# Fake server
# ---------------------------------------------------------------------------

def _block(q: str) -> str:
    return (
        "<memories>\n"
        f'<memory id="a1" relevance="0.81" age="2d">about {q}</memory>\n'
        '<memory id="b2" relevance="0.22" age="9d">weak match</memory>\n'
        '<memory id="c3" age="1d">no relevance attr</memory>\n'
        "</memories>"
    )


class FakeHttp:
    """Records every call; answers with documented server shapes."""

    def __init__(self):
        self._api_token = "tok"
        self.calls: list[tuple[str, str, dict | None]] = []

    def post(self, path, payload=None, timeout=None):
        self.calls.append(("POST", path, payload))
        if path == "/v1/memory/augmented-prompt":
            q = payload["query"]
            return {
                "context_block": _block(q),
                "memory_ids": ["a1", "b2", "c3"],
                # server F11 (2.17.x): per-memory text for A1
                "memories": [
                    {"id": "a1", "text": "about " + q},
                    {"id": "b2", "text": "weak match"},
                    {"id": "c3", "text": "no relevance attr"},
                ],
            }
        if path == "/v1/memory/list":
            return {"memories": []}
        if path == "/v1/memory/search":
            return {"results": [
                {"id": "x9", "text": "the old fact about rate limits",
                 "score": 0.71, "source_role": "user"},
                {"id": "y8", "text": "another candidate", "score": 0.40},
            ]}
        return {}

    def get(self, path, params=None, timeout=None):
        self.calls.append(("GET", path, params))
        return {}

    def delete(self, path, timeout=None):
        self.calls.append(("DELETE", path, None))
        return {"deleted": True}

    def n(self, method: str, path: str) -> int:
        return sum(1 for m, p, _ in self.calls if m == method and p == path)


@pytest.fixture
def provider():
    p = am.AstralCoreMemoryProvider()
    p._http = FakeHttp()
    p._server_healthy = True
    p._active_session_id = "s1"
    p._pool = None            # _submit becomes a no-op; ingest never leaves
    p.on_turn_start(1, "first message", session_id="s1")
    return p


# ---------------------------------------------------------------------------
# Version / contract
# ---------------------------------------------------------------------------

def test_version_is_2_12_0():
    assert am._VERSION == "2.12.0"


def test_contract_table_unchanged():
    # The ABC-facing signatures did not move in 2.12.0.
    assert set(am._CONTRACT) == {
        "prefetch", "queue_prefetch", "sync_turn", "handle_tool_call",
        "on_pre_compress", "on_session_switch",
    }
    assert am._assert_contract(am.AstralCoreMemoryProvider()) is True


def test_defaults():
    p = am.AstralCoreMemoryProvider()
    assert p._prefetch_mode == "live"
    assert p._inject_floor == 0.0
    assert p._t0_vacuity_filter is True
    assert p._capture_skipped_vacuous == 0


# ---------------------------------------------------------------------------
# P1 — live-turn prefetch
# ---------------------------------------------------------------------------

def test_live_mode_warm_makes_no_network_call(provider):
    provider.queue_prefetch("completed turn N-1", session_id="s1")
    assert provider._http.n("POST", "/v1/memory/augmented-prompt") == 0
    assert "s1" not in provider._prefetch_cache


def test_live_mode_prefetch_computes_the_live_query(provider):
    provider.queue_prefetch("completed turn N-1", session_id="s1")
    block = provider.prefetch("live turn N", session_id="s1")
    assert "about live turn N" in block
    assert "about completed turn N-1" not in block
    assert provider._http.n("POST", "/v1/memory/augmented-prompt") == 1


def test_live_mode_one_retrieval_per_message(provider):
    # warm (no call) + serve (one call) — the 2.16.1 day-0 duplication is gone
    provider.queue_prefetch("m1", session_id="s1")
    provider.prefetch("m2", session_id="s1")
    assert provider._http.n("POST", "/v1/memory/augmented-prompt") == 1


def test_warm_mode_is_2_11_3_behaviour(provider):
    provider._prefetch_mode = "warm"
    provider.queue_prefetch("old q", session_id="s1")
    assert provider._http.n("POST", "/v1/memory/augmented-prompt") == 1
    block = provider.prefetch("new q", session_id="s1")
    # stale block served, no second call — exactly what 2.11.3 did
    assert "about old q" in block
    assert provider._http.n("POST", "/v1/memory/augmented-prompt") == 1


def test_live_mode_serves_cache_when_qhash_matches(provider):
    # Fill the cache the warm way, then serve in live mode with the same text.
    provider._prefetch_mode = "warm"
    provider.queue_prefetch("same q", session_id="s1")
    provider._prefetch_mode = "live"
    block = provider.prefetch("same q", session_id="s1")
    assert "about same q" in block
    assert provider._http.n("POST", "/v1/memory/augmented-prompt") == 1


def test_live_mode_discards_stale_cache_and_recomputes(provider, caplog):
    provider._prefetch_mode = "warm"
    provider.queue_prefetch("q1", session_id="s1")
    provider._prefetch_mode = "live"
    with caplog.at_level("INFO", logger="astral-memory"):
        block = provider.prefetch("q2", session_id="s1")
    assert "about q2" in block and "about q1" not in block
    assert provider._http.n("POST", "/v1/memory/augmented-prompt") == 2
    assert any("event=discard" in r.getMessage() for r in caplog.records)
    assert "s1" not in provider._prefetch_cache


def test_serve_line_carries_mode(provider, caplog):
    with caplog.at_level("INFO", logger="astral-memory"):
        provider.prefetch("anything", session_id="s1")
    serve = [r.getMessage() for r in caplog.records if "event=serve" in r.getMessage()]
    assert serve and "mode=live" in serve[-1] and "stale=false" in serve[-1]


def test_invalid_prefetch_mode_falls_back_to_live(tmp_path):
    (tmp_path / "astral-memory.json").write_text(json.dumps({"prefetch_mode": "sideways"}))
    p = am.AstralCoreMemoryProvider()
    p._hermes_home = str(tmp_path)
    p._load_config()
    assert p._prefetch_mode == "live"


# ---------------------------------------------------------------------------
# Inject floor
# ---------------------------------------------------------------------------

def test_floor_helper_no_op_without_memory_elements():
    assert am._apply_inject_floor("server preamble only", 0.5) == \
        ("server preamble only", [], [], False)


def test_floor_helper_zero_is_off():
    blk = _block("x")
    assert am._apply_inject_floor(blk, 0.0) == (blk, [], [], False)


def test_floor_helper_drops_below_keeps_unknown():
    out, kept, dropped, parsed = am._apply_inject_floor(_block("x"), 0.5)
    assert parsed is True
    assert kept == ["a1", "c3"]       # c3 has no relevance → kept
    assert dropped == ["b2"]
    assert 'id="b2"' not in out and 'id="a1"' in out and 'id="c3"' in out
    assert "<memories>" in out        # framing untouched


def test_floor_off_by_default_block_byte_identical(provider):
    block = provider.prefetch("q", session_id="s1")
    assert block == _block("q")
    assert provider._recalled_ids["s1"] == ["a1", "b2", "c3"]


def test_floor_filters_block_ids_and_texts(provider, caplog):
    provider._inject_floor = 0.5
    with caplog.at_level("INFO", logger="astral-memory"):
        block = provider.prefetch("q", session_id="s1")
    assert 'id="b2"' not in block and 'id="a1"' in block
    assert provider._recalled_ids["s1"] == ["a1", "c3"]
    assert set(provider._served_texts["s1"]) == {"a1", "c3"}
    msgs = [r.getMessage() for r in caplog.records]
    assert any("astral_inject_floor" in m and "dropped=1" in m and "ids=b2" in m for m in msgs)
    assert any("event=compute" in m and "floor_dropped=1" in m for m in msgs)


def test_floor_above_everything_injects_nothing(provider):
    provider._inject_floor = 0.9
    block = provider.prefetch("q", session_id="s1")
    # c3 (no relevance) is kept by rule, so the block is not empty here;
    # make the server return only scored elements to hit the empty path.
    assert 'id="c3"' in block and 'id="a1"' not in block
    provider._http.post = lambda path, payload=None, timeout=None: (
        {"context_block": '<memories>\n<memory id="a1" relevance="0.3">t</memory>\n</memories>',
         "memory_ids": ["a1"], "memories": [{"id": "a1", "text": "t"}]}
        if path == "/v1/memory/augmented-prompt" else {"memories": []})
    block = provider.prefetch("q2", session_id="s1")
    assert block == ""
    assert provider._recalled_ids["s1"] == []
    assert provider._served_texts["s1"] == {}


def test_floor_out_of_range_disabled(tmp_path):
    (tmp_path / "astral-memory.json").write_text(json.dumps({"inject_floor": 1.7}))
    p = am.AstralCoreMemoryProvider()
    p._hermes_home = str(tmp_path)
    p._load_config()
    assert p._inject_floor == 0.0


# ---------------------------------------------------------------------------
# T0 vacuity pre-filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("user,asst", [
    ("Thanks!", "You're welcome."),
    ("ok", "👍"),
    ("Kiitos!", "Ole hyvä."),
    ("yes", "Great."),
    ("/new", "Started a new session."),
    ("", ""),
])
def test_vacuous_turns(user, asst):
    assert am._is_vacuous_turn(user, asst) is True


@pytest.mark.parametrize("user,asst", [
    ("Mina the Hollower", "Noted."),                       # N-10a case: proper noun
    ("Tommi", "Selvä."),                                   # FI proper noun
    ("thanks", "The deploy runs at 04:00 via a systemd timer on bot-jarmo."),
    ("yes", "Then the rate limit is 1000 requests per hour per key."),
    ("what's the rate limit?", "1000/hr."),
])
def test_non_vacuous_turns(user, asst):
    assert am._is_vacuous_turn(user, asst) is False


def test_sync_turn_skips_vacuous_after_fidelity(provider, caplog):
    captured = []
    provider._submit = lambda fn, *a, **k: captured.append(fn)
    provider.prefetch("q", session_id="s1")  # served set for fidelity
    with caplog.at_level("INFO", logger="astral-memory"):
        provider.sync_turn("Thanks!", "You're welcome.", session_id="s1")
    assert provider._capture_skipped_vacuous == 1
    assert captured == []                                   # no ingest submitted
    msgs = [r.getMessage() for r in caplog.records]
    assert any("skipped vacuous turn" in m for m in msgs)
    # fidelity observed the turn regardless (hook runs before every gate)
    assert any("rt13_fidelity: hook=after_turn" in m for m in msgs)
    assert "s1" in provider._fidelity_pending


def test_sync_turn_ingests_non_vacuous(provider):
    captured = []
    provider._submit = lambda fn, *a, **k: captured.append(fn)
    provider.sync_turn("Mina the Hollower", "Noted.", session_id="s1")
    assert provider._capture_skipped_vacuous == 0
    assert len(captured) == 1


def test_vacuity_filter_can_be_disabled(provider):
    provider._t0_vacuity_filter = False
    captured = []
    provider._submit = lambda fn, *a, **k: captured.append(fn)
    provider.sync_turn("Thanks!", "You're welcome.", session_id="s1")
    assert len(captured) == 1 and provider._capture_skipped_vacuous == 0


def test_astral_sync_surfaces_counters(provider):
    provider._capture_skipped_vacuous = 3
    r = json.loads(provider.handle_tool_call("astral_sync", {}, session_id="s1"))
    assert r["capture_skipped_vacuous"] == 3
    assert r["prefetch_mode"] == "live" and r["inject_floor"] == 0.0
    assert "capture_skipped_distilled" in r


# ---------------------------------------------------------------------------
# astral_forget
# ---------------------------------------------------------------------------

def test_forget_requires_an_argument(provider):
    r = json.loads(provider.handle_tool_call("astral_forget", {}, session_id="s1"))
    assert "error" in r
    assert not any(m == "DELETE" for m, _, _ in provider._http.calls)


def test_forget_by_query_never_deletes(provider):
    r = json.loads(provider.handle_tool_call(
        "astral_forget", {"query": "rate limits"}, session_id="s1"))
    assert r["deleted"] is False
    assert [c["memory_id"] for c in r["candidates"]] == ["x9", "y8"]
    assert r["candidates"][0]["provenance"] == "[user]"
    assert not any(m == "DELETE" for m, _, _ in provider._http.calls)
    search = [p for m, path, p in provider._http.calls if path == "/v1/memory/search"]
    assert search and search[0]["no_side_effects"] is True and search[0]["limit"] == 5


def test_forget_by_query_respects_namespace(provider):
    provider._server_supports_namespace = True
    provider._active_namespace = "warhammer-40k"
    provider.handle_tool_call("astral_forget", {"query": "orks"}, session_id="s1")
    search = [p for m, path, p in provider._http.calls if path == "/v1/memory/search"]
    assert search[0]["namespace_filter"] == "warhammer-40k"


def test_forget_by_id_deletes_and_scrubs_session_state(provider):
    provider._recalled_ids["s1"] = ["a1", "x9"]
    provider._served_texts["s1"] = {"x9": "old fact", "a1": "keep"}
    r = json.loads(provider.handle_tool_call(
        "astral_forget", {"memory_id": "x9"}, session_id="s1"))
    assert r["forgotten"] is True and r["memory_id"] == "x9"
    assert ("DELETE", "/v1/memory/x9", None) in provider._http.calls
    assert provider._recalled_ids["s1"] == ["a1"]
    assert "x9" not in provider._served_texts["s1"]


def test_forget_precedence_memory_id_over_source_over_query(provider):
    provider.handle_tool_call(
        "astral_forget", {"memory_id": "x9", "source": "s", "query": "q"}, session_id="s1")
    assert ("DELETE", "/v1/memory/x9", None) in provider._http.calls
    assert not any(path.startswith("/v1/memory/source/") for _, path, _ in provider._http.calls)
    assert not any(path == "/v1/memory/search" for _, path, _ in provider._http.calls)


def test_forget_by_source_unchanged(provider):
    provider.handle_tool_call("astral_forget", {"source": "hermes_session_42"}, session_id="s1")
    assert ("DELETE", "/v1/memory/source/hermes_session_42", None) in provider._http.calls


@pytest.mark.parametrize("bad", ["../x", "a/b", "x" * 129])
def test_forget_rejects_malformed_id(provider, bad):
    r = json.loads(provider.handle_tool_call("astral_forget", {"memory_id": bad}, session_id="s1"))
    assert "error" in r
    assert not any(m == "DELETE" for m, _, _ in provider._http.calls)


# ---------------------------------------------------------------------------
# RT-13 served set is turn-scoped
# ---------------------------------------------------------------------------

def test_served_set_is_turn_scoped(provider):
    provider.prefetch("turn one", session_id="s1")
    assert set(provider._served_texts["s1"]) == {"a1", "b2", "c3"}
    provider.sync_turn("turn one", "reply one", session_id="s1")
    # consumed by after_turn
    assert provider._served_texts["s1"] == {}
    assert provider._fidelity_pending["s1"]["served"] == ["a1", "b2", "c3"]
    # a recall result between turns is this turn's; the next compute resets
    provider._remember_served("s1", [{"id": "r7", "text": "recalled"}])
    provider.on_turn_start(2, "turn two", session_id="s1")
    provider.prefetch("turn two", session_id="s1")
    assert "r7" not in provider._served_texts["s1"]
    assert set(provider._served_texts["s1"]) == {"a1", "b2", "c3"}


def test_recall_adds_to_current_turn_served_set(provider):
    provider.prefetch("turn one", session_id="s1")
    provider.handle_tool_call("astral_recall", {"query": "rate"}, session_id="s1")
    assert {"a1", "b2", "c3", "x9", "y8"} <= set(provider._served_texts["s1"])
    provider.sync_turn("turn one", "the old fact about rate limits", session_id="s1")
    served = provider._fidelity_pending["s1"]["served"]
    assert "x9" in served and "a1" in served


# ---------------------------------------------------------------------------
# Distillation engine against astral_distillation v2.0.0
# ---------------------------------------------------------------------------

@pytest.mark.skipif(am._distill is None, reason="astral_distillation not shipped")
def test_automatic_distillation_engine_constructs(tmp_path):
    p = am.AstralCoreMemoryProvider()
    p._hermes_home = str(tmp_path)
    (tmp_path / "astral-memory.json").write_text(json.dumps({
        "distillation_mode": "on_session_start", "server_url": "http://127.0.0.1:1"}))
    # health probe fails fast (port 1) → no session-start pass is submitted,
    # but the engine must still be built — it was AttributeError before.
    p.initialize("s1", hermes_home=str(tmp_path))
    try:
        assert p._distill_engine is not None
        assert isinstance(p._distill_engine.client, am._distill.DigestClient)
    finally:
        p.shutdown()


@pytest.mark.skipif(am._distill is None, reason="astral_distillation not shipped")
def test_run_distillation_reads_v2_render_result(tmp_path, caplog):
    p = am.AstralCoreMemoryProvider()

    class Eng:
        def run(self):
            return am._distill.RenderResult(wrote=True, generation=4,
                                            files={str(tmp_path / "MEMORY.md"): "x"})
    p._distill_engine = Eng()
    with caplog.at_level("INFO", logger="astral-memory"):
        p._run_distillation()
    assert any("distilled digest gen=4" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Config load / save
# ---------------------------------------------------------------------------

def test_config_load_2_12_0_keys(tmp_path):
    (tmp_path / "astral-memory.json").write_text(json.dumps({
        "prefetch_mode": "warm", "inject_floor": "0.4", "t0_vacuity_filter": False}))
    p = am.AstralCoreMemoryProvider()
    p._hermes_home = str(tmp_path)
    p._load_config()
    assert p._prefetch_mode == "warm"
    assert p._inject_floor == 0.4
    assert p._t0_vacuity_filter is False


def test_save_config_coerces_2_12_0_keys(tmp_path):
    p = am.AstralCoreMemoryProvider()
    p.save_config({"prefetch_mode": "bogus", "inject_floor": "2",
                   "t0_vacuity_filter": "true", "server_url": "http://x"},
                  str(tmp_path))
    d = json.loads((tmp_path / "astral-memory.json").read_text())
    assert d["prefetch_mode"] == "live"
    assert d["inject_floor"] == 0.0
    assert d["t0_vacuity_filter"] is True
    assert d["server_url"] == "http://x"


def test_config_schema_declares_new_keys():
    keys = {e["key"] for e in am.AstralCoreMemoryProvider().get_config_schema()}
    assert {"prefetch_mode", "inject_floor", "t0_vacuity_filter"} <= keys
