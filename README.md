# Generation Timestamp: 2026-04-29T09:00:00Z

# Astral Core Memory — Hermes Agent Memory Provider

Offline-first persistent memory for Hermes Agent. No API keys required.

Your agent remembers across sessions, learns what matters, and works
entirely on your machine.

> **Get the memory server** — [orbitalfortress.com](https://orbitalfortress.com) · €19 one-time · macOS · Windows · Linux

---

## Quick Start

### 1. Install the plugin

Copy the plugin directory into the Hermes source tree. Hermes discovers
memory providers from `plugins/memory/<name>/` inside the Hermes repo.

```bash
# Clone this repo
git clone https://github.com/Suo-commerce/memory-hermes.git

# Copy into Hermes (note: directory MUST be astral_memory with underscore)
cp -r memory-hermes/astral-memory \
  ~/Projects/hermes-agent/plugins/memory/astral_memory
```

> **Why underscore?** Hermes matches `memory.provider` in config against
> the directory name. Python can't import hyphenated names, and Hermes
> uses the directory name as the import path. The source repo uses
> `astral-memory/` (hyphen) but the installed directory must be
> `astral_memory` (underscore).

### 2. Set the provider

```bash
hermes config set memory.provider astral_memory
```

### 3. Start the memory server

```bash
# Download (macOS Apple Silicon)
curl -L https://orbitalfortress.com/download/macos -o astral-memory-server
chmod +x astral-memory-server

# First run — activate license
./astral-memory-server --activate SOUL-XXXX-XXXX-XXXX-XXXX

# Start
./astral-memory-server
```

Verify it's running:

```bash
curl http://localhost:8090/health
```

### 4. Start the embedding server

The memory server needs a local embedding model for semantic search:

```bash
llama-server \
  --model nomic-embed-text-v1.5.Q5_K_M.gguf \
  --port 8081 \
  --embedding \
  -b 4096 -ub 4096
```

> **Important:** Always set `-b` and `-ub` to the same value to avoid
> silent embedding dimension clamping.

### 5. Launch Hermes

```bash
hermes
```

Verify the plugin loaded:

```
/plugins
```

You should see `astral-memory v2.0.0` in the list. Test it:

```
use astral_stats to show memory statistics
```

---

## What's New in v2.0.0

- **MemoryProvider ABC** — proper Hermes memory provider integration
  instead of the general plugin API. Shows up in `/plugins`, gets
  full lifecycle hooks.
- **Non-blocking sync** — conversation capture runs in daemon threads.
  Zero latency added to the agent loop.
- **Pre-compress capture** — when Hermes compresses old context,
  insights are saved to long-term memory before they're discarded.
- **MEMORY.md mirroring** — writes to Hermes's built-in MEMORY.md
  and USER.md are mirrored into Astral Core for unified search.
- **Circuit breaker** — if the memory server is down, the plugin
  stops hammering it and recovers gracefully.

---

## How It Works

### Memory Provider Lifecycle

```
User types message
  → Hermes calls prefetch(user_message)
  → Plugin fetches relevant memories from server
  → Memories injected into the current turn's context
  → LLM responds (with memory-augmented context)
  → Hermes calls sync_turn(user_message, assistant_response)
  → Plugin sends the turn to memory server (background thread)
  → Memory server runs surprise gate
  → Only novel information is stored
```

### Surprise-Gated Learning

Not everything gets stored. The memory engine uses a Delta Rule matrix
to predict incoming information against what it already knows. Only
genuinely novel content passes the surprise gate. Repeated information
is automatically filtered.

### Five-Tier Memory Lifecycle

```
Fast (today) → Medium (multi-session) → Slow (long-term)
                                              ↓
                                        Dormant (cold storage)
                                              ↓
                                        Archival (signal absorbed)
```

### Session Diary

Every session is automatically summarised via `on_session_end`. The
agent doesn't need to replay conversation history — the diary tells
it what happened and where you left off.

---

## Agent Tools

| Tool | What it does |
|------|-------------|
| `astral_recall` | Semantic search across all memory tiers |
| `astral_store` | Explicitly store a fact, preference, or decision |
| `astral_forget` | Delete all memories from a specific source |
| `astral_briefing` | Get a briefing card summary on demand |
| `astral_diary` | Write or read session diary entries |
| `astral_stats` | Full memory system statistics and health |
| `astral_sync` | Sync with Orbital Fortress (when configured) |

### Automatic Behaviour

In addition to the manual tools, two lifecycle methods run automatically:

- **Auto-recall** (`prefetch`) — searches memory for content relevant
  to the user's latest message and injects it into the agent's context.

- **Auto-capture** (`sync_turn`) — sends each conversation turn through
  the surprise-gated pipeline in a daemon thread. Only novel information
  is stored. Non-blocking — zero latency added to the agent loop.

---

## Configuration

### Environment variable override

```bash
export ASTRAL_SERVER_URL=http://localhost:9090
```

### Config file

Config is stored in `~/.hermes/astral-memory.json`:

```json
{
  "server_url": "http://localhost:8090",
  "auto_capture": true,
  "auto_recall": true,
  "max_recall_memories": 5,
  "capture_max_chars": 8000
}
```

### Memory server flags

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8090` | Memory server port |
| `--embed-url` | `http://localhost:8081` | Embedding server URL |
| `--data-path` | `./data/mask` | Where memories are stored |
| `--activate KEY` | — | Activate with license key |
| `--no-license` | — | Skip license (debug builds only) |

---

## Repository Structure

```
memory-hermes/
├── astral-memory/          ← Plugin source (copy as astral_memory/)
│   ├── __init__.py         ← MemoryProvider implementation
│   ├── schemas.py          ← Tool schemas for LLM
│   ├── tools.py            ← Deprecated (v1 compat stub)
│   └── plugin.yaml         ← Plugin manifest
├── skills/
│   └── astral-memory.md    ← Agent skill file
├── pyproject.toml
├── README.md
└── LICENSE
```

### Install mapping

| Source repo path | Install path in Hermes |
|---|---|
| `astral-memory/` | `plugins/memory/astral_memory/` |
| `skills/astral-memory.md` | `~/.hermes/skills/astral-memory.md` |

---

## Troubleshooting

### Plugin shows as `×` in `/plugins`

The config key must match the directory name exactly:

```bash
# WRONG — hyphen
hermes config set memory.provider astral-memory

# CORRECT — underscore (matches directory name)
hermes config set memory.provider astral_memory
```

### "Connection refused" on localhost:8090

Start the memory server:

```bash
./astral-memory-server
```

### No memories being stored

Check that the embedding server is running on port 8081. Memories
can't be stored without embeddings.

### Search returns nothing useful

If the embedding model changed since memories were stored:

```bash
./astral-memory-server --rebuild-embeddings
```

### Circuit breaker triggered

If the memory server goes down, the plugin stops sending requests
after 3 consecutive failures and retries after 60 seconds. Restart
the memory server and it recovers automatically.

---

## Coming from OpenClaw?

Both plugins share the same memory backend on `localhost:8090` — zero
migration needed. Your memory corpus, briefing cards, and diary entries
all carry over.

---

## Competitive Comparison

| Feature | Hermes built-in | Mem0 | **Astral Core** |
|---------|----------------|------|----------------|
| Works offline | Yes | No | **Yes** |
| Embeddings | FTS5 only | API required | **Local (nomic)** |
| Write intelligence | Store everything | Store everything | **Surprise-gated** |
| Memory lifecycle | None | None | **5-tier + dormancy** |
| Session diary | No | No | **Yes** |
| Cross-device sync | No | No | **Yes (Fortress)** |
| Cost after install | $0 | ~$5-15/mo | **$0** |

---

## Architecture

Astral Core Memory is part of the [Astral Core](https://github.com/Suo-commerce/astralcore)
project — a memory engine built for privacy-first, offline-capable
AI assistants.

| Component | What it does |
|-----------|-------------|
| Memory engine (MASK/HOPE) | Surprise-gated write pipeline |
| Memory API server | REST endpoints on :8090 |
| This Hermes plugin | MemoryProvider bridge |
| [OpenClaw plugin](https://github.com/Suo-commerce/memory-openclaw) | Bridge to OpenClaw |
| Orbital Fortress | Fleet sync server |

Both Hermes and OpenClaw plugins share the same memory backend.
Switch agents without losing a single memory.

---

## Links

- [Get a license](https://orbitalfortress.com) — €19, one-time
- [OpenClaw Plugin](https://github.com/Suo-commerce/memory-openclaw)
- [Report an issue](https://github.com/Suo-commerce/memory-hermes/issues)

## License

MIT — see [LICENSE](./LICENSE) for details.

The plugin is open source. The Astral Core memory server binary
requires a [license](https://orbitalfortress.com) (€19 one-time).

---

<div align="center">

**Your AI should remember you without phoning home.**

[Get Started](#quick-start) · [Orbital Fortress](https://orbitalfortress.com) · [Report Issue](https://github.com/Suo-commerce/memory-hermes/issues)

</div>
