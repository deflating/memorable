---
name: memory-management
description: This skill should be used when the user asks about past conversations, wants to remember something important, asks "when did we talk about X", "do you remember Y", or discusses memory, knowledge graph, session history, or context across sessions. Also use when something significant happens mid-conversation that should be preserved.
version: 0.1.0
---

# Memory Management

Memorable provides persistent memory across Claude Code sessions.

## When to Use

- **Searching memory**: User asks about past conversations → use `memorable_search_sessions`
- **Searching observations**: Tool usage and user prompts → use `memorable_search_observations`
- **System check**: Want to know memory status → use `memorable_get_system_status`

## Memory Hygiene

Don't record:
- Troubleshooting dead-ends
- Permission prompts or tool noise
- Routine file operations
- Ephemeral greetings
- Anything already captured by the automated transcript processing pipeline
