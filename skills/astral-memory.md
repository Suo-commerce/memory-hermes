# Generation Timestamp: 2026-04-10T04:00:00Z
---
name: astral-memory
version: 1.0.0
description: >
  When and how to use Astral Core persistent memory tools.
  Covers recall, storage, diary, and the "know before speaking" protocol.
---

# Astral Core Memory — Agent Skill

You have access to persistent long-term memory through Astral Core.
Memories survive across sessions. Use them.

## The Rule: Know Before Speaking

**BEFORE responding about any person, project, past decision, or
preference — call `astral_recall` FIRST.** Never guess from context
when you can verify from memory. This is the single most important
habit for memory-augmented agents.

Good:
- User asks "what database do we use?" → `astral_recall("database")` → answer with confidence
- User says "continue where we left off" → `astral_recall("last session")` + check diary

Bad:
- User asks about their deployment setup → you guess based on common patterns
- User references "the auth bug" → you improvise instead of searching

## When to Use Each Tool

### `astral_recall` — Search Memory
Use **before answering** questions about:
- The user's preferences, tools, stack, team
- Past decisions and their rationale
- Project details, architecture, deployment
- Anything discussed in previous sessions
- Names, dates, configurations mentioned before

Do NOT use for:
- General knowledge questions ("what is Kubernetes?")
- Information the user just provided in this message
- Simple greetings or small talk

### `astral_store` — Explicit Storage
Use **only when the user explicitly asks** you to remember something:
- "Remember that I prefer tabs over spaces"
- "Store this: our deploy target is us-east-1"
- "Don't forget — the deadline moved to April 15"

Do NOT use for:
- Routine conversation (auto-capture handles this)
- Information that's already stored (recall first to check)
- Temporary or session-specific context

### `astral_briefing` — Session Orientation
Use at the **start of a session** or when you need a quick overview:
- "What do you know about me?"
- "Give me a summary of our work"
- When you feel disoriented about the user's context

The briefing card is injected automatically at session start, but
you can request an updated one mid-session if significant memories
were added.

### `astral_diary` — Session Audit Trail
Use to **record milestones** and **check session history**:

Write:
- Completed a significant task → `astral_diary(action="write", text="Deployed auth service to staging", entry_type="milestone")`
- Hit a blocker → `astral_diary(action="write", text="CORS issue on /api/auth — needs origin whitelist", entry_type="error")`
- User makes a decision → `astral_diary(action="write", text="Decided to use PostgreSQL over MongoDB", entry_type="note")`

Read:
- "What did we do last session?" → `astral_diary(action="read", limit=5)`
- Check for previous errors → `astral_diary(action="read", entry_type="error")`

### `astral_forget` — Delete Memories
Use **only when the user explicitly asks** to forget something:
- "Forget everything from yesterday's import"
- "Delete the bulk-import memories"

Always confirm before deleting. This is irreversible.

### `astral_stats` — System Health
Use when the user asks about memory system status:
- "How many memories do you have?"
- "Is the memory system working?"
- Debugging recall issues

### `astral_sync` — Fleet Sync
Use when the user asks to sync across devices:
- "Sync my memories"
- "Pull updates from my other machine"

Only works when Orbital Fortress is configured.

## Patterns to Follow

1. **Recall before responding** about anything personal or project-specific
2. **Don't over-store** — auto-capture handles routine content
3. **Write diary milestones** when something significant is completed
4. **Check the diary** when the user asks "where did we leave off?"
5. **One recall per topic** — don't spam 5 recalls in one turn
6. **Trust the briefing card** for session orientation
7. **Confirm before forgetting** — deletion is permanent
