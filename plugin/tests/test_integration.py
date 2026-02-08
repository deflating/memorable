"""Integration tests for the notes pipeline.

Tests the full end-to-end pipeline from JSONL → formatted note,
as well as edge cases and error handling.
"""

import json
import pytest
from pathlib import Path

from server.notes import (
    extract_session_data,
    build_fact_sheet,
    select_key_moments,
    _format_note,
    _clean_text,
    _extract_signal_text,
    _looks_like_human_text,
    _short_path,
    _header_to_tags,
)


# ── End-to-end smoke test ─────────────────────────────────────


def test_full_pipeline_smoke_test(sample_jsonl_transcript):
    """Test the full pipeline from JSONL → formatted note (no DB, no API).

    This is a smoke test that verifies:
    - extract_session_data() runs without errors
    - build_fact_sheet() produces a valid string
    - select_key_moments() produces a list
    - _format_note() produces valid markdown with YAML frontmatter
    """
    # Step 1: Extract session data
    data = extract_session_data(sample_jsonl_transcript)

    assert data is not None
    assert isinstance(data, dict)
    assert "messages" in data
    assert "user_messages" in data
    assert "assistant_messages" in data
    assert "tool_calls" in data
    assert "tool_counts" in data
    assert "files_touched" in data
    assert "errors" in data
    assert "action_descriptions" in data
    assert "message_count" in data
    assert "user_word_count" in data
    assert "total_word_count" in data

    # Step 2: Build fact sheet
    fact_sheet = build_fact_sheet(data)

    assert isinstance(fact_sheet, str)
    assert len(fact_sheet) > 0
    # Fact sheet should contain expected sections
    assert "OPENING REQUEST:" in fact_sheet or "KEY ACTIONS" in fact_sheet

    # Step 3: Select key moments
    key_moments = select_key_moments(data, max_quotes=5)

    assert isinstance(key_moments, list)
    # Should have found at least one key moment from the sample transcript
    assert len(key_moments) >= 1
    for moment in key_moments:
        assert "role" in moment
        assert "text" in moment
        assert moment["role"] in ("user", "assistant")

    # Step 4: Format the note
    note_content = _format_note(
        date="2026-02-08",
        tags=["🔧 Testing", "✅ Progress"],
        mood="Building tests with determination",
        continuity=5,
        summary="A test session where we built comprehensive tests for the notes pipeline.",
        key_moments=key_moments,
        notes=[
            "**Unfinished:** ModuleNotFoundError: No module named 'pytest'",
            "**Files changed:** server/notes.py",
            "**Scope:** 1 edits, 2 searches, 1 commands"
        ]
    )

    assert isinstance(note_content, str)
    assert len(note_content) > 0

    # Verify YAML frontmatter
    assert note_content.startswith("---")
    assert "date: 2026-02-08" in note_content
    assert "tags:" in note_content
    assert "mood:" in note_content
    assert "continuity:" in note_content

    # Verify markdown sections
    assert "## Summary" in note_content
    assert "## Key Moments" in note_content
    assert "## Notes for Future Me" in note_content


def test_integration_with_real_data_structure(extracted_session_data):
    """Test that extracted_session_data has the correct structure."""
    data = extracted_session_data

    # Verify messages
    assert len(data["messages"]) > 0
    assert len(data["user_messages"]) > 0
    assert len(data["assistant_messages"]) > 0

    # Verify tool calls
    assert len(data["tool_calls"]) > 0
    for tc in data["tool_calls"]:
        assert "id" in tc
        assert "name" in tc
        assert "input" in tc

    # Verify tool counts
    assert isinstance(data["tool_counts"], dict)
    assert "Read" in data["tool_counts"] or "Bash" in data["tool_counts"]

    # Verify files touched
    assert isinstance(data["files_touched"], list)

    # Verify errors detected
    assert isinstance(data["errors"], list)
    # The sample transcript has a pytest ModuleNotFoundError
    assert len(data["errors"]) == 1
    assert "pytest" in data["errors"][0]["error"].lower() or "module" in data["errors"][0]["error"].lower()

    # Verify counts
    assert data["message_count"] > 0
    assert data["user_word_count"] > 0
    assert data["total_word_count"] >= data["user_word_count"]


# ── Edge cases ────────────────────────────────────────────────


def test_empty_jsonl_file(empty_jsonl_transcript):
    """Test that an empty JSONL file returns zero counts."""
    data = extract_session_data(empty_jsonl_transcript)

    assert data is not None
    assert data["message_count"] == 0
    assert data["user_word_count"] == 0
    assert data["total_word_count"] == 0
    assert len(data["messages"]) == 0
    assert len(data["user_messages"]) == 0
    assert len(data["assistant_messages"]) == 0
    assert len(data["tool_calls"]) == 0
    assert len(data["files_touched"]) == 0
    assert len(data["errors"]) == 0


def test_malformed_jsonl_file(malformed_jsonl_transcript):
    """Test that malformed JSON lines are skipped gracefully."""
    data = extract_session_data(malformed_jsonl_transcript)

    # Should have extracted the 2 valid lines, skipped the malformed ones
    assert data is not None
    assert data["message_count"] == 2
    assert len(data["user_messages"]) == 1
    assert len(data["assistant_messages"]) == 1


def test_unicode_in_messages(unicode_jsonl_transcript):
    """Test that unicode characters are handled correctly."""
    data = extract_session_data(unicode_jsonl_transcript)

    assert data is not None
    assert len(data["messages"]) == 2

    # Check that unicode content is preserved
    user_text = data["user_messages"][0]["text"]
    assert "😊" in user_text or "café" in user_text or "你好" in user_text

    assistant_text = data["assistant_messages"][0]["text"]
    assert "ñoño" in assistant_text or "Zürich" in assistant_text or "Москва" in assistant_text


def test_very_long_messages(very_long_message_transcript):
    """Test that very long messages (>2000 chars) are handled without crash."""
    data = extract_session_data(very_long_message_transcript)

    assert data is not None
    assert len(data["messages"]) == 2
    assert len(data["user_messages"]) == 1

    # Message should be preserved (not truncated at extraction)
    user_text = data["user_messages"][0]["text"]
    assert len(user_text) > 2000


def test_only_assistant_messages(only_assistant_messages_transcript):
    """Test JSONL with only assistant messages, no user messages."""
    data = extract_session_data(only_assistant_messages_transcript)

    assert data is not None
    assert len(data["user_messages"]) == 0
    assert len(data["assistant_messages"]) == 2
    assert data["user_word_count"] == 0
    assert data["total_word_count"] > 0


def test_select_key_moments_with_no_user_messages():
    """Test select_key_moments when there are no user messages."""
    data = {
        "user_messages": [],
        "assistant_messages": [
            {"text": "I am talking to myself."}
        ]
    }

    moments = select_key_moments(data)

    assert isinstance(moments, list)
    assert len(moments) == 0  # No user messages, so no key moments


def test_build_fact_sheet_with_minimal_data():
    """Test build_fact_sheet with minimal data (no actions, no errors)."""
    data = {
        "user_messages": [{"text": "Hello"}],
        "assistant_messages": [{"text": "Hi there"}],
        "action_descriptions": [],
        "files_touched": [],
        "tool_counts": {},
        "errors": [],
        "message_count": 2,
        "user_word_count": 1,
        "total_word_count": 3,
    }

    fact_sheet = build_fact_sheet(data)

    assert isinstance(fact_sheet, str)
    assert len(fact_sheet) > 0
    # Should at least have stats
    assert "STATS:" in fact_sheet
    assert "2 messages" in fact_sheet


# ── Utility function tests ────────────────────────────────────


def test_clean_text_removes_system_reminders():
    """Test that _clean_text removes <system-reminder> tags."""
    text = "Hello <system-reminder>This is a system message</system-reminder> world"
    cleaned = _clean_text(text)

    assert "<system-reminder>" not in cleaned
    assert "Hello" in cleaned
    assert "world" in cleaned
    assert "This is a system message" not in cleaned


def test_extract_signal_text():
    """Test that _extract_signal_text extracts the message from Signal."""
    text = "[Signal from team-lead] Here's your next task"
    extracted = _extract_signal_text(text)

    assert extracted == "Here's your next task"


def test_extract_signal_text_none_when_no_signal():
    """Test that _extract_signal_text returns None when no Signal."""
    text = "This is a regular message"
    extracted = _extract_signal_text(text)

    assert extracted is None


def test_looks_like_human_text_filters_system_artifacts():
    """Test that _looks_like_human_text filters out system artifacts."""
    assert not _looks_like_human_text("You are a helpful assistant...")
    assert not _looks_like_human_text("PROGRESS SUMMARY: All done")
    assert not _looks_like_human_text("Contents of /path/to/file.py")
    assert not _looks_like_human_text("Called the function successfully")

    # Should accept normal human text
    assert _looks_like_human_text("Hey Claude, can you help me with this?")
    assert _looks_like_human_text("This is frustrating!")


def test_looks_like_human_text_filters_code():
    """Test that _looks_like_human_text filters out file contents and code."""
    # Code with multiple def statements (>2) should be filtered
    code = "def foo():\n    return 42\n\ndef bar():\n    pass\n\ndef baz():\n    return 1"
    assert not _looks_like_human_text(code)

    # Code with multiple import statements (>3) should be filtered
    imports = "import os\nimport sys\nimport json\nimport pathlib"
    assert not _looks_like_human_text(imports)

    # File content with line numbers (→ arrows) should be filtered
    file_content = "1→ import json\n2→ import sys\n3→ from pathlib import Path"
    assert not _looks_like_human_text(file_content)


def test_short_path():
    """Test that _short_path returns last 2 path components."""
    assert _short_path("/Users/claude/memorable/plugin/server/notes.py") == "server/notes.py"
    assert _short_path("/Users/claude/file.py") == "claude/file.py"
    assert _short_path("file.py") == "file.py"
    assert _short_path("") == ""


def test_header_to_tags():
    """Test that _header_to_tags splits header into tag list."""
    header = "🔧 Built auth | 💛 Felt good | ✅ Chose JWT"
    tags = _header_to_tags(header)

    assert isinstance(tags, list)
    assert len(tags) == 3
    assert "🔧 Built auth" in tags
    assert "💛 Felt good" in tags
    assert "✅ Chose JWT" in tags


def test_header_to_tags_empty():
    """Test that _header_to_tags handles empty header."""
    assert _header_to_tags("") == []
    assert _header_to_tags(None) == []


def test_header_to_tags_truncates():
    """Test that _header_to_tags truncates to max 6 tags."""
    header = " | ".join([f"Tag {i}" for i in range(10)])
    tags = _header_to_tags(header)

    assert len(tags) <= 6


# ── Format validation tests ───────────────────────────────────


def test_format_note_yaml_frontmatter(sample_key_moments):
    """Test that _format_note produces valid YAML frontmatter."""
    note = _format_note(
        date="2026-02-08",
        tags=["🔧 Testing", "✅ Progress"],
        mood="Focused and determined",
        continuity=7,
        summary="Built comprehensive tests for the notes pipeline.",
        key_moments=sample_key_moments,
        notes=["**Unfinished:** One test still failing"]
    )

    lines = note.split("\n")

    # First line should be ---
    assert lines[0] == "---"

    # Find the closing ---
    closing_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line == "---":
            closing_idx = i
            break

    assert closing_idx is not None, "YAML frontmatter should have closing ---"

    # Extract frontmatter
    frontmatter = "\n".join(lines[1:closing_idx])

    assert "date: 2026-02-08" in frontmatter
    assert "tags:" in frontmatter
    assert "mood:" in frontmatter
    assert "continuity: 7" in frontmatter


def test_format_note_sections(sample_key_moments):
    """Test that _format_note includes all required sections."""
    note = _format_note(
        date="2026-02-08",
        tags=["🔧 Testing"],
        mood="Focused",
        continuity=5,
        summary="This is a summary paragraph.",
        key_moments=sample_key_moments,
        notes=["**Note:** Something important"]
    )

    assert "## Summary" in note
    assert "This is a summary paragraph." in note

    assert "## Key Moments" in note
    # Should have Matt's quote
    assert "**Matt:**" in note
    # Should have Claude's quote
    assert "**Claude:**" in note

    assert "## Notes for Future Me" in note
    assert "**Note:** Something important" in note


def test_format_note_with_annotations(sample_key_moments):
    """Test that _format_note includes annotations when present."""
    # sample_key_moments already has an annotation on the second quote
    note = _format_note(
        date="2026-02-08",
        tags=[],
        mood="Test",
        continuity=5,
        summary="Summary",
        key_moments=sample_key_moments,
        notes=[]
    )

    # The second moment has an annotation
    assert "emotional frustration surfacing" in note
    # Annotations should be italicized
    assert "*emotional frustration surfacing*" in note


def test_format_note_empty_moments_and_notes():
    """Test that _format_note handles empty moments and notes."""
    note = _format_note(
        date="2026-02-08",
        tags=[],
        mood="Quiet",
        continuity=5,
        summary="A silent session.",
        key_moments=[],
        notes=[]
    )

    # Should still have frontmatter and summary
    assert "date: 2026-02-08" in note
    assert "## Summary" in note
    assert "A silent session." in note

    # Key Moments and Notes sections may or may not be present if empty
    # The implementation adds them but with no content
