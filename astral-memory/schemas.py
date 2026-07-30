# Generation Timestamp: 2026-07-30T20:00:00Z
"""
Tool schemas — what the LLM reads to decide when to call Astral Core tools.
Version: 2.4.0

These are returned by AstralCoreMemoryProvider.get_tool_schemas() and
injected into the Hermes tool registry automatically.  The LLM sees
them alongside built-in tools.

v2.4.0 CHANGES (SPEC-NAMESPACE-001):
  NEW  — `namespace` parameter on astral_recall and astral_store.
         Pass a specific namespace slug to scope the operation, or "all"
         on recall to search across all namespaces.
  NEW  — ASTRAL_NAMESPACE schema for the astral_namespace tool, which
         sets, queries, or clears the active memory namespace for the
         session.
"""

ASTRAL_RECALL = {
    "name": "astral_recall",
    "description": (
        "Search your long-term memory for relevant information. Use this before "
        "answering questions about the user's preferences, past decisions, project "
        "details, or anything discussed in previous sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default: 5)",
            },
            "namespace": {
                "type": "string",
                "description": (
                    "Namespace to search within. Omit to use the session's "
                    "active namespace. Pass \"all\" to search across all "
                    "namespaces (cross-namespace search)."
                ),
            },
        },
        "required": ["query"],
    },
}

ASTRAL_STORE = {
    "name": "astral_store",
    "description": (
        "Explicitly store a fact, preference, or decision in long-term memory. "
        "Use this for important information the user wants remembered. Routine "
        "conversation content is captured automatically — only use this for "
        "explicit 'remember this' requests."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The information to store",
            },
            "category": {
                "type": "string",
                "description": "Category: fact, preference, decision, pattern (default: fact)",
            },
            "namespace": {
                "type": "string",
                "description": (
                    "Namespace to store this memory in. Omit to use the "
                    "session's active namespace."
                ),
            },
        },
        "required": ["text"],
    },
}

ASTRAL_FORGET = {
    "name": "astral_forget",
    "description": (
        "Delete all memories from a specific source. Use when the user asks "
        "to forget imported data or a specific session's memories."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Source identifier to delete (e.g. 'hermes_session_42')",
            },
        },
        "required": ["source"],
    },
}

ASTRAL_BRIEFING = {
    "name": "astral_briefing",
    "description": (
        "Get the current briefing card — a short summary of who the user is, "
        "what they're working on, and which knowledge areas are strong or sparse. "
        "Useful at the start of a session or when you need a quick orientation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "max_tokens": {
                "type": "integer",
                "description": "Maximum tokens for the card (default: 200)",
            },
        },
    },
}

ASTRAL_DIARY = {
    "name": "astral_diary",
    "description": (
        "Read or write session diary entries. The diary is a structured audit "
        "trail of what happened across sessions. Write milestones, errors, or "
        "notes. Read to see what happened in previous sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["write", "read"],
                "description": "Whether to write a new entry or read existing ones",
            },
            "text": {
                "type": "string",
                "description": "Entry text (required for write)",
            },
            "entry_type": {
                "type": "string",
                "enum": ["note", "summary", "error", "milestone"],
                "description": "Entry type (default: note)",
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to read (default: 10, for read action)",
            },
        },
        "required": ["action"],
    },
}

ASTRAL_STATS = {
    "name": "astral_stats",
    "description": (
        "Get memory system statistics: total memories, tier distribution, "
        "category health, embedding backend status, and diary entry count."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

ASTRAL_SYNC = {
    "name": "astral_sync",
    "description": (
        "Trigger a sync with Orbital Fortress to push local memories and "
        "pull briefings from other devices. Requires fortress_url to be "
        "configured. Returns sync status and briefing count."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

ASTRAL_NAMESPACE = {
    "name": "astral_namespace",
    "description": (
        "Set or query the active memory namespace for this session. "
        "A namespace partitions memories by context (project, topic, "
        "game world). When set, all memory operations are scoped to "
        "that namespace. When unset, all namespaces are searched."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "get", "clear"],
                "description": (
                    "set — activate a namespace; "
                    "get — show the current namespace; "
                    "clear — deactivate (switch to cross-namespace mode)."
                ),
            },
            "namespace": {
                "type": "string",
                "description": (
                    "Namespace slug (lowercase alphanumeric with hyphens). "
                    "Required for 'set'."
                ),
            },
        },
        "required": ["action"],
    },
}
