<!-- Generation Timestamp: 2026-07-09T00:00:00Z -->
<!--
  v2.2.0 CHANGES:
    NEW  — Compatibility matrix. The absence of one is why the v2.1.0 outage
           was invisible: nothing told a user their plugin and their Hermes
           disagreed.
    NEW  — Upgrade notice for the silent-memory-failure bug.
    NEW  — Troubleshooting entry for "memory stopped working after update",
           listed first because it is the current top support issue.
    FIX  — `/plugins` output now reads v2.2.0.
    FIX  — Config block documents every key the code actually reads
           (briefing_on_start, min_similarity, data_dir were undocumented).
    FIX  — Lifecycle section documents session_id-scoped calls and the hooks
           added in v2.2.0 (on_session_switch, on_delegation, backup_paths).
    FIX  — "daemon threads" -> bounded worker pool.
    FIX  — Repository structure reflects tests/ and .github/; tools.py removed.

  UNRESOLVED — see the note at the bottom of this file before publishing.
    `rerank_server.py` is referenced in Quick Start, Repository Structure,
    Install Mapping, and Troubleshooting, but does not exist in this repo.
    Either vendor it or strip those four sections. Do not ship this README
    until that is settled.
-->

# Astral Core Memory — Hermes Agent Memory Provider

Offline-first persistent memory for Hermes Agent. No API keys required.

Your agent remembers across sessions, learns what matters, and works
entirely on your machine.

> **Get the memory server** — [orbitalfortress.com](https://orbitalfortress.com) · €19 one-time · macOS · Windows · Linux

---

## Compatibility

Hermes changed the `MemoryProvider` interface in v0.18.0. Plugin versions are
not interchangeable across that boundary.

| Hermes Agent | Plugin version | Status |
|---|---|---|
| `>= v2026.7.1` (v0.18.0) | **2.2.0** | Supported |
| `v0.17.x` and earlier | 2.1.0 | End of life — no further fixes |
| Any | 2.1.0 on Hermes ≥ v0.18.0 | **Broken. Memory silently does nothing.** |

Check your versions:

```bash
hermes --version
hermes plugins   # look for: astral-memory v2.2.0
```

### If you are on plugin 2.1.0 with Hermes v0.18.0 or newer

Your memory has not been recording, and nothing has been recalled into
context, since you updated Hermes. The agent kept working, so there was
nothing to see.

Hermes v0.18.0 began passing a `session_id` keyword to `prefetch()`,
`queue_prefetch()` and `sync_turn()`. Plugin 2.1.0 did not accept it, so every
call raised `TypeError`. Hermes catches provider exceptions so that a failing
memory backend can never break your turn — at `DEBUG` level for recall, and
`WARNING` for capture. The result was a total memory outage with no traceback
and no error message.

Upgrade the plugin (below). Memories stored before the Hermes update are
intact and will be recalled again immediately. Conversations that happened
during the outage were never captured and cannot be recovered.

---

## Quick Start

### 1. Install the plugin

Copy the plugin directory into the Hermes source tree. Hermes discovers
memory providers from `plugins/memory/<name>/` inside the Hermes repo.

```bash
git clone https://github.com/Suo-commerce/memory-hermes.git

# Note: the installed directory MUST be astral_memory (underscore)
cp -r memory-hermes/astral-memory \
  ~/Projects/hermes-agent/plugins/memory/astral_memory
```

> **Why underscore?** Hermes matches `memory.provider` in config against the
> directory name, and uses that name as the Python import path. Python cannot
> import hyphenated names. The source repo uses `astral-memory/` (hyphen);
> the installed directory must be `astral_memory` (underscore).

Upgrading? Remove the old directory first, so a stale `tools.py` or `.pyc`
cannot shadow the new module:

```bash
rm -rf ~/Projects/hermes-agent/plugins/memory/astral_memory
cp -r memory-hermes/astral-memory \
  ~/Projects/hermes-agent/plugins/memory/astral_memory
```

### 2. Set the provider

```bash
hermes config set memory.provider astral_memory
```

### 3. Start the local services

#### 3a. Embedding server

```bash
llama-server \
  --model nomic-embed-text-v1.5.Q5_K_M.gguf \
  --port 8081 \
  --embedding \
  -b 4096 -ub 4096
```

> **Important:** always set `-b` and `-ub` to the same value. Mismatched
> values cause silent batch clamping, and embeddings degrade without error.

#### 3b. Reranker sidecar (optional, recommended)

The reranker re-scores candidate memories with a cross-encoder. It uses
`cross-encoder/ms-marco-MiniLM-L-6-v2` (22M params, CPU-only, ~5ms per query)
and auto-downloads the model (~90MB) on first run.

```bash
pip install sentence-transformers flask
python3 rerank_server.py --port 8082
```

Without it, retrieval still works via cosine similarity — ranking is simply
less accurate.

#### 3c. Memory server

```bash
# macOS Apple Silicon
curl -L https://orbitalfortress.com/download/macos -o astral-memory-server
chmod +x astral-memory-server

# First run — activate
./astral-memory-server --activate SOUL-XXXX-XXXX-XXXX-XXXX

# Start with the full pipeline
./astral-memory-server \
  --embed-url http://localhost:8081 \
  --deep-rerank \
  --deep-rerank-url http://localhost:8082 \
  --hyde
```

Without the reranker sidecar, omit the `--deep-rerank` flags:

```bash
./astral-memory-server --embed-url http://localhost:8081
```

Verify:

```bash
curl http://localhost:8090/health
```

### 4. Launch Hermes

```bash
hermes
```

Then, inside the agent:

```
/plugins
```

You should see `astral-memory v2.2.0`. Confirm it can reach the server:

```
use astral_stats to show memory statistics
```

If `astral_stats` returns memory counts, the bridge is live. If it returns an
error, the plugin loaded but the memory server is unreachable — see
Troubleshooting.

---

## What's New

### v2.2.0 — contract repair

- **Fixes the silent memory outage on Hermes ≥ v0.18.0.** `prefetch()`,
  `queue_prefetch()` and `sync_turn()` now accept the `session_id` keyword
  Hermes passes on every call.
- **Correct session scoping.** The active session id is now updated on
  `/new`, `/reset`, `/branch`, `/resume` and context compression. Previously
  it was captured once at startup, so every memory written after a session
  switch carried a stale source tag.
- **Per-session recall isolation.** The prefetch cache is keyed by session id.
  Concurrent gateway sessions can no longer read one another's context.
- **Subagent results are captured.** Hermes runs subagents with
  `skip_memory=True`, so the parent provider is the only place a delegation
  result can land. They were previously discarded.
- **`hermes backup` now captures the corpus** via `backup_paths()`.
- **Compression contributions.** `on_pre_compress()` tells the compressor that
  the span has been persisted, so it can preserve decisions rather than prose.
- **A contract guard runs at startup.** If a future Hermes release changes the
  interface, you get a loud `ERROR` in the log instead of silence.

### v2.1.0

- Cross-encoder reranking via the MiniLM sidecar.
- HyDE query expansion (`--hyde`).
- All retrieval intelligence moved server-side; the plugin stays a thin bridge.

### v2.0.0

- `MemoryProvider` ABC integration — appears in `/plugins`, full lifecycle hooks.
- Non-blocking capture.
- Pre-compress capture.
- `MEMORY.md` / `USER.md` mirroring.
- Circuit breaker on a dead server.

---

## How It Works

### Lifecycle

```
User types message
  → Hermes calls prefetch(message, session_id=...)
  → Plugin returns cached context, or fetches it synchronously
  → Server runs: HyDE → embedding search → cross-encoder rerank → expand
  → Memories injected into this turn's context
  → LLM responds

  → Hermes calls sync_turn(user, assistant, session_id=..., messages=[...])
  → Plugin queues the turn on a bounded worker pool
  → Server runs the surprise gate; only novel information is stored

  → Hermes calls queue_prefetch(message, session_id=...)
  → Plugin warms the cache for the next turn
```

Session boundaries (`on_session_switch`), subagent returns (`on_delegation`),
context compression (`on_pre_compress`) and session end (`on_session_end`) are
all handled. Background work runs on a bounded pool — a slow or wedged memory
server cannot stall the agent loop, and a dead one cannot break a turn.

### Surprise-Gated Learning

Not everything is stored. The engine uses a Delta Rule matrix to predict
incoming information against what it already knows. Only genuinely novel
content passes the gate; repetition is filtered out.

### Five-Tier Memory Lifecycle

```
Fast (today) → Medium (multi-session) → Slow (long-term)
                                              ↓
                                        Dormant (cold storage)
                                              ↓
                                        Archival (signal absorbed)
```

### Session Diary

Each session is summarised on exit. The agent does not replay history — the
diary tells it what happened and where you left off.

---

## Agent Tools

| Tool | What it does |
|------|-------------|
| `astral_recall` | Semantic search across all memory tiers |
| `astral_store` | Explicitly store a fact, preference, or decision |
| `astral_forget` | Delete all memories from a specific source |
| `astral_briefing` | Get a briefing card summary on demand |
| `astral_diary` | Write or read session diary entries |
| `astral_stats` | Memory system statistics and health |
| `astral_sync` | Sync with Orbital Fortress (when configured) |

Auto-recall and auto-capture run without the agent invoking anything. The
tools are for when it needs to reach for memory deliberately.

---

## Configuration

### Environment overrides

```bash
export ASTRAL_SERVER_URL=http://localhost:9090
export ASTRAL_DATA_DIR=~/.astral        # used by `hermes backup`
```

### Config file

`~/.hermes/astral-memory.json` — or run `hermes memory setup` and answer the
prompts.

```json
{
  "server_url": "http://localhost:8090",
  "data_dir": "~/.astral",
  "auto_capture": true,
  "auto_recall": true,
  "max_recall_memories": 5,
  "capture_max_chars": 8000,
  "briefing_on_start": false,
  "min_similarity": null
}
```

| Key | Default | Description |
|---|---|---|
| `server_url` | `http://localhost:8090` | Memory server address |
| `data_dir` | `~/.astral` | Corpus location; declared to `hermes backup` |
| `auto_capture` | `true` | Send each turn through the surprise gate |
| `auto_recall` | `true` | Inject relevant memories before each turn |
| `max_recall_memories` | `5` | Memories injected per turn |
| `capture_max_chars` | `8000` | Truncation limit per message |
| `briefing_on_start` | `false` | Prepend the briefing card on a session's first turn |
| `min_similarity` | unset | Floor for recall; unset defers to the server |

### Memory server flags

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8090` | Memory server port |
| `--embed-url` | `http://localhost:8081` | Embedding server URL |
| `--deep-rerank` | off | Enable cross-encoder reranking |
| `--deep-rerank-url` | `http://localhost:8082` | Reranker sidecar URL |
| `--hyde` | off | Enable HyDE query expansion |
| `--data-path` | `./data/mask` | Where memories are stored |
| `--activate KEY` | — | Activate with a license key |
| `--no-license` | — | Skip license (debug builds only) |

### Reranker sidecar flags

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8082` | Reranker port |
| `--model` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model |
| `--host` | `0.0.0.0` | Bind address |

---

## Repository Structure

```
memory-hermes/
├── astral-memory/              ← Plugin source (install as astral_memory/)
│   ├── __init__.py             ← MemoryProvider implementation
│   ├── schemas.py              ← Tool schemas for the LLM
│   └── plugin.yaml             ← Plugin manifest
├── rerank_server.py            ← MiniLM reranker sidecar
├── skills/
│   └── astral-memory.md        ← Agent skill file
├── tests/
│   └── test_provider_contract.py   ← Guards against interface drift
├── .github/workflows/ci.yml    ← Pinned contract job + nightly upstream canary
├── pyproject.toml
├── README.md
└── LICENSE
```

### Install mapping

| Source repo path | Install path |
|---|---|
| `astral-memory/` | `<hermes-agent>/plugins/memory/astral_memory/` |
| `skills/astral-memory.md` | `~/.hermes/skills/astral-memory.md` |
| `rerank_server.py` | anywhere on `PATH` (runs standalone) |

---

## Troubleshooting

### Memory stopped working after updating Hermes

The most likely cause is a plugin/Hermes version mismatch — see
[Compatibility](#compatibility). Plugin 2.1.0 on Hermes ≥ v0.18.0 fails
silently. Upgrade to 2.2.0.

To confirm, run Hermes with debug logging and look for provider errors:

```bash
hermes --log-level DEBUG 2>&1 | grep -i "astral\|memory provider"
```

A line reading `prefetch failed (non-fatal)` or `sync_turn failed` on every
turn is the signature. From 2.2.0 onward, an incompatible build logs an
explicit `ERROR` naming the offending method at startup.

### Plugin shows as `×` in `/plugins`

The config key must match the directory name exactly:

```bash
hermes config set memory.provider astral-memory   # WRONG — hyphen
hermes config set memory.provider astral_memory   # CORRECT — underscore
```

### `ImportError: agent.memory_provider`

The plugin is installed outside a Hermes tree, or Hermes is too old. From
2.2.0 this is deliberately fatal rather than silently degrading — an earlier
fallback stub is exactly what let the v2.1.0 outage go unnoticed.

### "Connection refused" on `localhost:8090`

The memory server is not running. Start it (step 3c). The plugin will log a
warning at startup and memory will be inactive for the session.

### "Connection refused" on `localhost:8082`

The reranker sidecar is not running. The memory server falls back to
cosine-only ranking; retrieval works, less accurately.

### No memories being stored

Check the embedding server on port 8081. Without embeddings, nothing can be
written. Then check `astral_stats` — if `total_memories` is climbing, capture
is working and the issue is retrieval, not storage.

### Search returns nothing useful

If the embedding model changed since your memories were written:

```bash
./astral-memory-server --rebuild-embeddings
```

### Circuit breaker triggered

After 3 consecutive failures the plugin stops sending requests for 60 seconds,
then retries. Restart the memory server; it recovers on its own.

### Reranker model download slow

First run downloads ~90MB from Hugging Face. Pre-download:

```bash
python3 -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
```

---

## Coming from OpenClaw?

Both plugins share the same memory backend on `localhost:8090` — no migration.
Your corpus, briefing cards and diary entries carry over.

---

## Comparison

| Feature | Hermes built-in | Mem0 | **Astral Core** |
|---------|----------------|------|----------------|
| Works offline | Yes | No | **Yes** |
| Embeddings | FTS5 only | API required | **Local (nomic)** |
| Cross-encoder reranking | No | No | **Yes (MiniLM)** |
| Write intelligence | Store everything | Store everything | **Surprise-gated** |
| Memory lifecycle | None | None | **5-tier + dormancy** |
| Session diary | No | No | **Yes** |
| Cross-device sync | No | No | **Yes (Fortress)** |
| Cost after install | $0 | ~$5–15/mo | **$0** |

---

## Architecture

| Component | Port | What it does |
|-----------|------|-------------|
| Embedding server | 8081 | nomic-embed-text via llama-server |
| Reranker sidecar | 8082 | MiniLM cross-encoder |
| Memory server | 8090 | MASK/HOPE pipeline, REST API |
| This plugin | — | `MemoryProvider` bridge |
| [OpenClaw plugin](https://github.com/Suo-commerce/memory-openclaw) | — | Bridge to OpenClaw |
| Orbital Fortress | remote | Fleet sync |

---

## Contributing

The contract guard is not optional. If you change `astral-memory/__init__.py`:

```bash
git clone --depth 1 --branch v2026.7.1 \
  https://github.com/NousResearch/hermes-agent .hermes-src

ASTRAL_CONTRACT_REQUIRE=1 PYTHONPATH=.hermes-src pytest tests/ -v
```

`ASTRAL_CONTRACT_REQUIRE=1` turns a missing Hermes into a failure rather than
a skip. A skipped guard is a guard that has quietly stopped guarding — which
is the whole reason this repository now has one.

---

## Links

- [Get a license](https://orbitalfortress.com) — €19, one-time
- [OpenClaw plugin](https://github.com/Suo-commerce/memory-openclaw)
- [Report an issue](https://github.com/Suo-commerce/memory-hermes/issues)

## License

MIT — see [LICENSE](./LICENSE).

The plugin is open source. The Astral Core memory server binary requires a
[license](https://orbitalfortress.com) (€19 one-time).

---

<div align="center">

**Your AI should remember you without phoning home.**

[Get Started](#quick-start) · [Orbital Fortress](https://orbitalfortress.com) · [Report an issue](https://github.com/Suo-commerce/memory-hermes/issues)

</div>
