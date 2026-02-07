---
description: Search past sessions and knowledge graph
argument-hint: <search query>
allowed-tools: [memorable_search_sessions, memorable_query_kg]
---

# Memorable Search

Search through stored session notes and knowledge graph entries.

The user wants to search for: $ARGUMENTS

1. Use `memorable_search_sessions` with the query to find matching session notes
2. Also use `memorable_query_kg` with the entity name to find related knowledge graph entries
3. Present results from both sources, with the most relevant first
4. Include dates, continuity scores, and tags for context
