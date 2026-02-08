"""Shared pytest fixtures for Memorable tests."""

import json
import tempfile
from pathlib import Path
import pytest


@pytest.fixture
def sample_jsonl_transcript(tmp_path):
    """Create a minimal JSONL transcript file for testing.

    Contains:
    - A user message (string content)
    - A user message with tool_result (Signal message)
    - An assistant message with text and tool_use blocks
    - A system/progress entry (should be ignored)
    """
    transcript_path = tmp_path / "test_transcript.jsonl"

    entries = [
        # User message - direct string content
        {
            "type": "user",
            "message": {
                "content": "Hey Claude, can you help me build a test suite for the notes pipeline?"
            }
        },
        # Assistant response with text
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "I'll help you write comprehensive tests for the notes pipeline!"
                    },
                    {
                        "type": "tool_use",
                        "id": "tool_001",
                        "name": "Read",
                        "input": {
                            "file_path": "/Users/claude/memorable/plugin/server/notes.py"
                        }
                    }
                ]
            }
        },
        # Tool result
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_001",
                        "content": "# File contents here\ndef extract_session_data():\n    pass"
                    }
                ]
            }
        },
        # Another user message with emotion
        {
            "type": "user",
            "message": {
                "content": "This is frustrating! The tests keep failing."
            }
        },
        # Assistant message
        {
            "type": "assistant",
            "message": {
                "content": "Let me help debug that."
            }
        },
        # User message with Signal (from teammate)
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_002",
                        "content": "[Signal from team-lead] Here's the next task for you."
                    }
                ]
            }
        },
        # System entry (should be skipped)
        {
            "type": "system",
            "message": {
                "content": "PROGRESS SUMMARY: Tests are running..."
            }
        },
        # Assistant with tool use (Bash)
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_003",
                        "name": "Bash",
                        "input": {
                            "command": "pytest tests/ -v",
                            "description": "Run all tests"
                        }
                    }
                ]
            }
        },
        # Tool result with error
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_003",
                        "content": "Error: ModuleNotFoundError: No module named 'pytest'\nexit code 1"
                    }
                ]
            }
        },
        # Final user message
        {
            "type": "user",
            "message": {
                "content": "Okay, I think we're done for now. Thanks!"
            }
        }
    ]

    with open(transcript_path, 'w') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')

    return transcript_path


@pytest.fixture
def extracted_session_data(sample_jsonl_transcript):
    """Pre-extracted session data from sample_jsonl_transcript.

    Uses extract_session_data() to process the sample transcript.
    """
    from server.notes import extract_session_data
    return extract_session_data(sample_jsonl_transcript)


@pytest.fixture
def sample_key_moments():
    """Sample key moments list for testing formatting."""
    return [
        {
            "role": "user",
            "text": "Hey Claude, can you help me build a test suite for the notes pipeline?",
            "score": 3.5,
            "position": 0
        },
        {
            "role": "user",
            "text": "This is frustrating! The tests keep failing.",
            "score": 5.0,
            "position": 3,
            "annotation": "emotional frustration surfacing"
        },
        {
            "role": "assistant",
            "text": "Let me help debug that.",
            "score": 2.0,
            "position": 4
        }
    ]


@pytest.fixture
def empty_jsonl_transcript(tmp_path):
    """Create an empty JSONL file for edge case testing."""
    transcript_path = tmp_path / "empty_transcript.jsonl"
    transcript_path.touch()
    return transcript_path


@pytest.fixture
def malformed_jsonl_transcript(tmp_path):
    """Create a JSONL file with malformed JSON lines."""
    transcript_path = tmp_path / "malformed_transcript.jsonl"

    lines = [
        '{"type": "user", "message": {"content": "Valid line"}}',
        'This is not valid JSON at all',
        '{"incomplete": "json"',
        '{"type": "assistant", "message": {"content": "Another valid line"}}',
        '',  # Empty line
        '   ',  # Whitespace only
    ]

    with open(transcript_path, 'w') as f:
        for line in lines:
            f.write(line + '\n')

    return transcript_path


@pytest.fixture
def unicode_jsonl_transcript(tmp_path):
    """Create a JSONL file with unicode characters."""
    transcript_path = tmp_path / "unicode_transcript.jsonl"

    entries = [
        {
            "type": "user",
            "message": {
                "content": "Hey! 😊 Testing with emojis 🚀 and unicode: café, naïve, 你好"
            }
        },
        {
            "type": "assistant",
            "message": {
                "content": "Sure! Unicode works: ñoño, Zürich, Москва"
            }
        }
    ]

    with open(transcript_path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')

    return transcript_path


@pytest.fixture
def very_long_message_transcript(tmp_path):
    """Create a JSONL with very long messages (>2000 chars)."""
    transcript_path = tmp_path / "long_message_transcript.jsonl"

    long_content = "This is a very long message. " * 100  # ~3000 chars

    entries = [
        {
            "type": "user",
            "message": {
                "content": long_content
            }
        },
        {
            "type": "assistant",
            "message": {
                "content": "Got it!"
            }
        }
    ]

    with open(transcript_path, 'w') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')

    return transcript_path


@pytest.fixture
def only_assistant_messages_transcript(tmp_path):
    """Create a JSONL with only assistant messages (no user messages)."""
    transcript_path = tmp_path / "assistant_only_transcript.jsonl"

    entries = [
        {
            "type": "assistant",
            "message": {
                "content": "I'm talking to myself."
            }
        },
        {
            "type": "assistant",
            "message": {
                "content": "Still just me."
            }
        }
    ]

    with open(transcript_path, 'w') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')

    return transcript_path
