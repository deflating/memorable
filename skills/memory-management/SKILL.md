---
name: memory-management
description: This skill should be used when the user asks about past conversations, wants to remember something important, asks "when did we talk about X", "do you remember Y", or discusses memory, knowledge graph, session history, or context across sessions. Also use when something significant happens mid-conversation that should be preserved.
version: 0.1.0
---

# Memory Management

Memorable provides persistent memory across Claude Code sessions.

## When to Use

- **Searching memory**: User asks about past conversations → use `memorable_search_sessions`
- **Recording significant moments**: Important decision, discovery, or emotional moment → use `memorable_record_significant`
- **Knowledge graph queries**: Looking up structured facts about people, projects, decisions → use `memorable_query_kg`
- **System check**: Want to know memory status → use `memorable_get_system_status`

## Priority Scoring Guide

When recording to the knowledge graph:
- **10 (Sacred)**: Identity truths, core relationships, immutable facts. These are NEVER curated away.
- **7-9 (Important)**: Current job status, active projects, recent decisions. Temporal but significant.
- **4-6 (Contextual)**: Background facts, preferences, patterns. Useful but not critical.
- **1-3 (Ephemeral)**: Passing mentions, minor details. Only surfaced when specifically searched.

## Memory Hygiene

Don't record:
- Troubleshooting dead-ends
- Permission prompts or tool noise
- Routine file operations
- Ephemeral greetings
- Anything already captured by the automated transcript processing pipeline
