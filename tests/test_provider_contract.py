# Generation Timestamp: 2026-07-09T00:00:00Z
#
# tests/test_provider_contract.py
# v1.0.0 — CI guard against MemoryProvider contract drift.
#
# WHY THIS FILE EXISTS
#   astral-memory v2.1.0 shipped with `prefetch(self, query)` against a Hermes
#   ABC that had moved to `prefetch(self, query, *, session_id="")`. Every call
#   raised TypeError. MemoryManager caught it — at DEBUG level for prefetch,
#   WARNING for sync_turn — so the agent kept running and memory silently did
#   nothing. Users reported "memory stopped working after update"; the plugin
#   looked healthy, tools were registered, no traceback surfaced.
#
#   Signature drift in a duck-typed plugin boundary is invisible unless
#   something explicitly looks for it. This file looks for it.
#
# WHAT IT CHECKS
#   1. Every override is call-compatible with the ABC (parameter names + kinds).
#   2. Every abstractmethod is implemented.
#   3. Call-site simulation: the provider survives being invoked exactly the
#      way agent/memory_manager.py invokes it. This is the regression test —
#      (1) would have caught the v2.1.0 bug, but (3) catches drift in *how*
#      the manager calls us, which the ABC alone does not describe.
#   4. Return types the manager depends on (str, not None).
#   5. backup_paths() works without initialize() and without network.
#   6. Per-session prefetch isolation (no cross-session context leakage).
#   7. on_session_switch actually reassigns session state.
#
# RUNNING
#   Hermes must be importable. In CI:
#       pip install -e /path/to/hermes-agent
#   or add the checkout to PYTHONPATH. Set ASTRAL_CONTRACT_REQUIRE=1 to make a
#   missing Hermes a hard failure rather than a skip — do this in CI so the
#   guard cannot silently stop guarding.
#
#       ASTRAL_CONTRACT_REQUIRE=1 pytest tests/test_provider_contract.py -v

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Import the Hermes ABC
# ---------------------------------------------------------------------------

_REQUIRE = os.environ.get("ASTRAL_CONTRACT_REQUIRE", "").strip() in ("1", "true", "yes")

try:
    from agent.memory_provider import MemoryProvider  # type: ignore
    _HERMES_AVAILABLE = True
    _IMPORT_ERROR = ""
except ImportError as exc:  # pragma: no cover
    MemoryProvider = None  # type: ignore
    _HERMES_AVAILABLE = False
    _IMPORT_ERROR = str(exc)

if not _HERMES_AVAILABLE:
    if _REQUIRE:
        raise RuntimeError(
            "ASTRAL_CONTRACT_REQUIRE=1 but `agent.memory_provider` is not "
            f"importable ({_IMPORT_ERROR}). The contract guard cannot run. "
            "Install Hermes Agent (pip install -e path/to/hermes-agent) or "
            "add the checkout to PYTHONPATH."
        )
    pytest.skip(
        "Hermes Agent not importable — contract guard skipped. "
        "Set ASTRAL_CONTRACT_REQUIRE=1 to make this a failure.",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Import our plugin package
#
# The on-disk directory is hyphenated (`astral-memory/`), which is not a legal
# Python identifier, so a plain `import` will not find it. Load by path and
# register under the underscore name the package expects for `from . import
# schemas` to resolve.
# ---------------------------------------------------------------------------

def _load_plugin():
    repo_root = Path(__file__).resolve().parents[1]
    for dirname in ("astral_memory", "astral-memory"):
        pkg_dir = repo_root / dirname
        init = pkg_dir / "__init__.py"
        if init.exists():
            break
    else:
        raise RuntimeError(
            f"Could not locate the plugin package under {repo_root}. "
            "Expected astral_memory/ or astral-memory/."
        )

    if "astral_memory" in sys.modules:
        return sys.modules["astral_memory"]

    spec = importlib.util.spec_from_file_location(
        "astral_memory", init, submodule_search_locations=[str(pkg_dir)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["astral_memory"] = module
    spec.loader.exec_module(module)
    return module


astral_memory = _load_plugin()
AstralCoreMemoryProvider = astral_memory.AstralCoreMemoryProvider


# ---------------------------------------------------------------------------
# Fakes — no network, ever
# ---------------------------------------------------------------------------

class _FakeHttp:
    """Stands in for _HttpClient. Records calls, returns canned responses."""

    def __init__(self, base_url: str = "http://fake:8090"):
        self.base_url = base_url
        self.calls: List[tuple] = []

    def get(self, path: str, params: Optional[dict] = None, timeout: float = 0) -> dict:
        self.calls.append(("GET", path, params))
        if path == "/v1/memory/briefing":
            return {"briefing": "BRIEFING-CARD"}
        if path == "/v1/diary/read":
            return {"entries": []}
        if path == "/v1/memory/stats":
            return {"total_memories": 42}
        return {}

    def post(self, path: str, payload: Optional[dict] = None, timeout: float = 0) -> dict:
        self.calls.append(("POST", path, payload))
        if path == "/v1/memory/augmented-prompt":
            q = (payload or {}).get("query", "")
            return {"context_block": f"CONTEXT<{q}>"}
        if path == "/v1/memory/search":
            return {"results": []}
        return {"ok": True}

    def delete(self, path: str, timeout: float = 0) -> dict:
        self.calls.append(("DELETE", path, None))
        return {"deleted": 0}

    def health(self) -> dict:
        return {
            "status": "ok", "version": "test", "total_memories": 0,
            "embedding_backend": "fake", "deep_rerank_enabled": False,
            "hyde_enabled": False,
        }

    def paths(self, method: Optional[str] = None) -> List[str]:
        return [p for m, p, _ in self.calls if method is None or m == method]

    def payload_for(self, path: str) -> Optional[dict]:
        for _, p, body in self.calls:
            if p == path:
                return body
        return None


@pytest.fixture
def http() -> _FakeHttp:
    return _FakeHttp()


@pytest.fixture
def provider(monkeypatch, tmp_path, http):
    """An initialized provider wired to a fake transport."""
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url: http)
    p = AstralCoreMemoryProvider()
    p.initialize("session-alpha", hermes_home=str(tmp_path))
    yield p
    p.shutdown()


def _drain(p) -> None:
    """Block until background work submitted via _submit() has completed."""
    pool = p._pool
    if pool is not None:
        pool.shutdown(wait=True)
        p._pool = None


# ---------------------------------------------------------------------------
# 1. Signature compatibility with the ABC
# ---------------------------------------------------------------------------

# Methods the manager calls with keyword arguments the old plugin did not accept.
# Extend this list whenever Hermes adds a hook we implement.
_OVERRIDES = [
    "name", "is_available", "initialize",
    "get_tool_schemas", "handle_tool_call",
    "system_prompt_block", "prefetch", "queue_prefetch", "sync_turn",
    "on_turn_start", "on_session_end", "on_session_switch",
    "on_pre_compress", "on_delegation", "on_memory_write",
    "get_config_schema", "save_config", "backup_paths", "shutdown",
]


def _param_shape(func) -> List[tuple]:
    """(name, kind) pairs — ignores annotations and defaults."""
    return [(p.name, p.kind) for p in inspect.signature(func).parameters.values()]


@pytest.mark.parametrize("method", _OVERRIDES)
def test_override_matches_abc_signature(method: str):
    """Our override must accept exactly what the ABC declares.

    Annotations and default values are deliberately ignored — only parameter
    names and kinds determine whether a call succeeds.
    """
    base = getattr(MemoryProvider, method, None)
    assert base is not None, (
        f"MemoryProvider has no '{method}'. Either Hermes removed it (drop our "
        f"override) or the installed Hermes is too old for this plugin."
    )
    ours = getattr(AstralCoreMemoryProvider, method)

    if isinstance(base, property):
        assert isinstance(ours, property), f"'{method}' is a property on the ABC"
        return

    assert _param_shape(ours) == _param_shape(base), (
        f"{method}() signature drift.\n"
        f"  ABC:  {inspect.signature(base)}\n"
        f"  ours: {inspect.signature(ours)}\n"
        f"MemoryManager will call the ABC form; the mismatch raises TypeError, "
        f"which the manager swallows. Memory would silently stop working."
    )


def test_no_unimplemented_abstract_methods():
    """Instantiation fails loudly if an abstractmethod is missing."""
    assert not getattr(AstralCoreMemoryProvider, "__abstractmethods__", frozenset()), (
        "Unimplemented abstract methods: "
        f"{sorted(AstralCoreMemoryProvider.__abstractmethods__)}"
    )
    AstralCoreMemoryProvider()  # must not raise


def test_no_fallback_stub():
    """The ABC must be the real one, not a locally-defined stand-in.

    v2.1.0 defined a stub MemoryProvider in an `except ImportError` branch.
    It froze the old contract and is precisely why the drift went unnoticed.
    """
    assert AstralCoreMemoryProvider.__mro__[1] is MemoryProvider, (
        "AstralCoreMemoryProvider does not inherit from the real "
        "agent.memory_provider.MemoryProvider. A fallback stub is in play."
    )


# ---------------------------------------------------------------------------
# 2. Call-site simulation — how memory_manager.py actually invokes us
# ---------------------------------------------------------------------------

def test_prefetch_accepts_session_id_kwarg(provider):
    """memory_manager.py:507 — provider.prefetch(query, session_id=session_id)"""
    result = provider.prefetch("what did we decide", session_id="session-alpha")
    assert isinstance(result, str), "prefetch must return str, not None"
    assert "CONTEXT<" in result


def test_queue_prefetch_accepts_session_id_kwarg(provider):
    """memory_manager.py:535 — provider.queue_prefetch(query, session_id=...)"""
    provider.queue_prefetch("warm me", session_id="session-alpha")
    # Next prefetch must consume the warmed cache, not refetch.
    before = len(provider._http.calls)
    result = provider.prefetch("ignored", session_id="session-alpha")
    assert "CONTEXT<warm me>" in result
    assert len(provider._http.calls) == before, "cached prefetch should not hit the server"


def test_sync_turn_accepts_session_id_and_messages(provider, http):
    """memory_manager.py:596 — sync_turn(u, a, session_id=..., messages=[...])"""
    provider.sync_turn(
        "user says", "assistant says",
        session_id="session-alpha",
        messages=[{"role": "user", "content": "user says"}],
    )
    _drain(provider)
    assert "/v1/memory/ingest" in http.paths("POST")
    body = http.payload_for("/v1/memory/ingest")
    assert body["source"] == "hermes_session-alpha"


def test_sync_turn_accepts_session_id_without_messages(provider, http):
    """memory_manager.py:603 — the messages-less branch."""
    provider.sync_turn("u", "a", session_id="session-alpha")
    _drain(provider)
    assert "/v1/memory/ingest" in http.paths("POST")


def test_manager_detects_messages_support():
    """Mirrors MemoryManager._provider_sync_accepts_messages().

    If this returns False the manager silently drops the `messages` payload
    and we lose tool-call context from every captured turn.
    """
    sig = inspect.signature(AstralCoreMemoryProvider.sync_turn)
    params = list(sig.parameters.values())
    accepts = (
        any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params)
        or "messages" in sig.parameters
    )
    assert accepts, "sync_turn must accept `messages` or **kwargs"


def test_handle_tool_call_accepts_kwargs(provider):
    """memory_manager.py:750 — provider.handle_tool_call(tool_name, args, **kwargs)"""
    out = provider.handle_tool_call(
        "astral_recall", {"query": "x"}, session_id="session-alpha",
    )
    parsed = json.loads(out)
    assert "error" not in parsed


def test_handle_tool_call_first_param_is_tool_name():
    """The ABC names it `tool_name`. Positional callers don't care, but the
    manager may switch to keyword dispatch; keep the names aligned."""
    params = list(inspect.signature(AstralCoreMemoryProvider.handle_tool_call).parameters)
    assert params[1] == "tool_name"


def test_on_pre_compress_returns_str(provider):
    """The manager folds the return value into the compression prompt.
    Returning None is tolerated by truthiness checks but drops the feature."""
    messages = [
        {"role": "user", "content": "a question long enough to pass the filter"},
        {"role": "assistant", "content": "an answer"},
    ]
    result = provider.on_pre_compress(messages)
    _drain(provider)
    assert isinstance(result, str)
    assert result.strip(), "on_pre_compress should contribute to the summary prompt"


def test_system_prompt_block_returns_str(provider):
    assert isinstance(provider.system_prompt_block(), str)


def test_unhealthy_server_yields_empty_prompt_block(monkeypatch, tmp_path):
    class _Dead(_FakeHttp):
        def health(self):
            return None

    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url: _Dead())
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))
    assert p.system_prompt_block() == ""
    p.shutdown()


# ---------------------------------------------------------------------------
# 3. Tools
# ---------------------------------------------------------------------------

def test_every_declared_tool_has_a_handler(provider):
    """A schema with no dispatch entry is a tool the LLM can call into a hole."""
    for schema in provider.get_tool_schemas():
        name = schema["name"]
        out = json.loads(provider.handle_tool_call(name, _minimal_args(name)))
        assert out.get("error") != f"Unknown tool: {name}", f"{name} has no handler"


def _minimal_args(name: str) -> Dict[str, Any]:
    return {
        "astral_recall":  {"query": "q"},
        "astral_store":   {"text": "t"},
        "astral_forget":  {"source": "s"},
        "astral_diary":   {"action": "read"},
    }.get(name, {})


def test_tool_schemas_are_well_formed(provider):
    for schema in provider.get_tool_schemas():
        assert set(schema) >= {"name", "description", "parameters"}
        assert schema["parameters"]["type"] == "object"
        assert isinstance(schema["parameters"].get("properties", {}), dict)


def test_unknown_tool_returns_json_error(provider):
    out = json.loads(provider.handle_tool_call("astral_nonexistent", {}))
    assert "error" in out


def test_tool_call_before_initialize_returns_json_error():
    """Never raise into the agent loop, even uninitialized."""
    out = json.loads(AstralCoreMemoryProvider().handle_tool_call("astral_stats", {}))
    assert "error" in out


# ---------------------------------------------------------------------------
# 4. Session scoping
# ---------------------------------------------------------------------------

def test_prefetch_cache_is_per_session(provider):
    """A single shared cache slot leaked context between concurrent gateway
    sessions in <= 2.1.0."""
    provider.queue_prefetch("alpha secret", session_id="session-alpha")
    provider.queue_prefetch("beta secret", session_id="session-beta")

    assert "alpha secret" in provider.prefetch("x", session_id="session-alpha")
    assert "beta secret" in provider.prefetch("x", session_id="session-beta")


def test_session_switch_reassigns_active_session(provider, http):
    """Without on_session_switch, `_active_session_id` stayed frozen at whatever
    initialize() saw and every later write carried a stale source tag."""
    provider.on_session_switch("session-omega", reset=True)
    provider.sync_turn("u", "a")  # no explicit session_id — must use the new one
    _drain(provider)
    assert http.payload_for("/v1/memory/ingest")["source"] == "hermes_session-omega"


def test_session_switch_reset_clears_cache(provider):
    provider.queue_prefetch("stale", session_id="session-alpha")
    provider.on_session_switch("session-new", reset=True)
    assert provider._prefetch_cache == {}
    assert provider._turn_counts == {}


def test_session_switch_continuation_never_carries_context(provider):
    """/branch, /resume and compression keep the conversation but change the id.
    Turn counts may carry; recalled context must not."""
    provider.on_turn_start(7, "msg")
    provider.queue_prefetch("old context", session_id="session-alpha")
    provider.on_session_switch("session-branch", parent_session_id="session-alpha")

    assert "session-branch" not in provider._prefetch_cache
    assert "session-alpha" not in provider._prefetch_cache
    assert provider._turn_counts.get("session-branch") == 7


def test_delegation_is_captured(provider, http):
    """Subagents run with skip_memory=True — the parent is the only place
    delegation results can land. v2.1.0 discarded them entirely."""
    provider.on_delegation("do the thing", "did the thing", child_session_id="kid-1")
    _drain(provider)
    body = http.payload_for("/v1/memory/ingest")
    assert body is not None
    assert body["source"] == "hermes_delegation_kid-1"


def test_on_memory_write_accepts_metadata(provider, http):
    provider.on_memory_write(
        "add", "user", "prefers tabs",
        metadata={"write_origin": "tool", "session_id": "session-alpha"},
    )
    _drain(provider)
    assert http.payload_for("/v1/memory/ingest")["source"] == "hermes_builtin_user"


def test_on_memory_write_ignores_remove(provider, http):
    provider.on_memory_write("remove", "user", "x")
    _drain(provider)
    assert "/v1/memory/ingest" not in http.paths("POST")


# ---------------------------------------------------------------------------
# 5. backup_paths — no initialize(), no network
# ---------------------------------------------------------------------------

def test_backup_paths_without_initialize_or_network(monkeypatch):
    """Contract: 'MUST be callable without initialize() and without network.'
    `hermes backup` calls this on a bare instance."""
    class _Forbidden:
        ConnectionError = Exception
        Timeout = Exception
        HTTPError = Exception

        def __getattr__(self, _name):
            raise AssertionError("backup_paths() must not touch the network")

    monkeypatch.setattr(astral_memory, "requests", _Forbidden())

    paths = AstralCoreMemoryProvider().backup_paths()
    assert isinstance(paths, list)
    assert all(isinstance(p, str) and Path(p).is_absolute() for p in paths)


def test_backup_paths_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ASTRAL_DATA_DIR", str(tmp_path / "corpus"))
    assert str(tmp_path / "corpus") in AstralCoreMemoryProvider().backup_paths()


def test_is_available_without_network(monkeypatch):
    """Contract: no network calls in is_available()."""
    class _Forbidden:
        ConnectionError = Exception
        Timeout = Exception
        HTTPError = Exception

        def __getattr__(self, _name):
            raise AssertionError("is_available() must not touch the network")

    monkeypatch.setattr(astral_memory, "requests", _Forbidden())
    assert AstralCoreMemoryProvider().is_available() is True


# ---------------------------------------------------------------------------
# 6. Config
# ---------------------------------------------------------------------------

def test_save_config_merges_rather_than_clobbers(tmp_path):
    p = AstralCoreMemoryProvider()
    p.save_config({"server_url": "http://a:1"}, str(tmp_path))
    p.save_config({"auto_recall": "false"}, str(tmp_path))

    cfg = json.loads((tmp_path / "astral-memory.json").read_text())
    assert cfg["server_url"] == "http://a:1", "second save clobbered the first"
    assert cfg["auto_recall"] is False, "string booleans must be coerced"


def test_config_schema_keys_are_read_by_load_config(monkeypatch, tmp_path, http):
    """Every advertised config key must actually reach an attribute.
    The README documented keys the code never read."""
    (tmp_path / "astral-memory.json").write_text(json.dumps({
        "server_url": "http://x:9",
        "auto_capture": False,
        "auto_recall": False,
        "max_recall_memories": 11,
        "briefing_on_start": True,
        "data_dir": "/tmp/astral-corpus",
    }))
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url: http)
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))

    assert p._server_url == "http://x:9"
    assert p._auto_capture is False
    assert p._auto_recall is False
    assert p._max_recall == 11
    assert p._briefing_on_start is True
    assert p._data_dir == "/tmp/astral-corpus"
    p.shutdown()


def test_auto_recall_disabled_short_circuits(monkeypatch, tmp_path, http):
    (tmp_path / "astral-memory.json").write_text(json.dumps({"auto_recall": False}))
    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url: http)
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))
    assert p.prefetch("q", session_id="s") == ""
    assert http.calls == []
    p.shutdown()


# ---------------------------------------------------------------------------
# 7. Failure isolation — a dead server must never break a turn
# ---------------------------------------------------------------------------

def test_dead_server_never_raises(monkeypatch, tmp_path):
    class _Dead(_FakeHttp):
        def health(self):
            return None

        def get(self, path, params=None, timeout=0):
            return {"error": "Memory server not reachable"}

        def post(self, path, payload=None, timeout=0):
            return {"error": "Memory server not reachable"}

        def delete(self, path, timeout=0):
            return {"error": "Memory server not reachable"}

    monkeypatch.setattr(astral_memory, "_HttpClient", lambda url: _Dead())
    p = AstralCoreMemoryProvider()
    p.initialize("s", hermes_home=str(tmp_path))

    assert p.prefetch("q", session_id="s") == ""
    p.queue_prefetch("q", session_id="s")
    p.sync_turn("u", "a", session_id="s")
    p.on_turn_start(1, "m")
    p.on_session_end([{"role": "user", "content": "hi"}])
    assert isinstance(p.on_pre_compress([]), str)
    assert "error" in json.loads(p.handle_tool_call("astral_stats", {}))
    p.shutdown()


def test_shutdown_is_idempotent(provider):
    provider.shutdown()
    provider.shutdown()


# ---------------------------------------------------------------------------
# 8. The guard itself
# ---------------------------------------------------------------------------

def test_assert_contract_passes_on_current_provider(caplog):
    assert astral_memory._assert_contract(AstralCoreMemoryProvider()) is True


def test_assert_contract_catches_the_v2_1_0_regression(caplog):
    """Reconstruct the exact bug and confirm the guard would have caught it."""

    class _Regressed(AstralCoreMemoryProvider):
        def prefetch(self, query):  # type: ignore[override]  # the v2.1.0 signature
            return ""

    with caplog.at_level("ERROR", logger="astral-memory"):
        assert astral_memory._assert_contract(_Regressed()) is False

    messages = [r.getMessage() for r in caplog.records]
    assert any("prefetch" in m and "session_id" in m for m in messages), (
        f"guard did not name the offending method/parameter; got {messages}"
    )
