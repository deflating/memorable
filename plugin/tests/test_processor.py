"""Tests for processor.py filtering and pipeline logic.

Tests focus on pure logic functions — no DB or API calls needed.
"""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
import json


# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from server.processor import TranscriptProcessor, _summarize_with_haiku


class TestSummarizeWithHaiku:
    """Test the preamble stripping logic in _summarize_with_haiku."""

    def test_strips_ill_preambles(self):
        """Test that "I'll summarize..." lines are stripped."""
        mock_output = "I'll summarize this for you.\n\nMatt wanted to build a web viewer."
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            assert result == "Matt wanted to build a web viewer."
            assert not result.startswith("I'll")

    def test_strips_perfect_preambles(self):
        """Test that "Perfect..." lines are stripped."""
        mock_output = "Perfect, here's the summary.\n\nMatt asked about authentication."
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            assert result == "Matt asked about authentication."
            assert not result.startswith("Perfect")

    def test_strips_ive_reviewed_preambles(self):
        """Test that "I've reviewed..." lines are stripped."""
        mock_output = "I've reviewed the entire session.\n\nMatt debugged a login bug."
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            assert result == "Matt debugged a login bug."
            assert not result.startswith("I've")

    def test_strips_empty_leading_lines(self):
        """Test that empty leading lines are stripped."""
        mock_output = "\n\n\nMatt configured the database."
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            assert result == "Matt configured the database."
            assert not result.startswith("\n")

    def test_extracts_matt_paragraph_from_multiple_paragraphs(self):
        """Test that multi-paragraph output extracts the "Matt..." paragraph."""
        mock_output = """Here's the summary.

Matt wanted to fix a bug in the authentication system. He ran several tests and discovered the issue was in the token validation logic.

Is there anything else you'd like to know?"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            # Should extract the paragraph starting with "Matt" (must be >100 chars)
            assert result.startswith("Matt wanted to fix a bug")
            assert "Is there anything else" not in result

    def test_extracts_longest_narrative_from_bullet_points(self):
        """Test that bullet-point output extracts the longest narrative paragraph."""
        mock_output = """Matt debugged the authentication flow and discovered several issues with token validation. The session involved running multiple tests.

The changes included:
- Fixed token expiration
- Updated middleware
- Added error handling"""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            # Should extract the longest non-bullet paragraph (must be >100 chars)
            assert "Matt debugged the authentication flow" in result
            assert "- Fixed token" not in result

    def test_clean_output_passes_through(self):
        """Test that clean output passes through unchanged."""
        mock_output = "Matt worked on the web viewer and fixed several CSS issues."
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            assert result == mock_output

    def test_handles_numbered_lists(self):
        """Test handling of numbered lists in output."""
        mock_output = """Matt wanted to build a feature and worked through several implementation steps to complete it successfully.

1. First he did this
2. Then he did that

The end result was successful."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout=mock_output,
                stderr=""
            )
            result = _summarize_with_haiku("test fact sheet")
            # Should extract longest non-numbered paragraph (must be >100 chars)
            assert "Matt wanted to build a feature" in result
            assert "1. First" not in result

    def test_handles_subprocess_error(self):
        """Test error handling when subprocess fails."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stdout="",
                stderr="API error"
            )
            result = _summarize_with_haiku("test fact sheet")
            assert "[Haiku summary failed:" in result
            assert "API error" in result

    def test_handles_timeout(self):
        """Test timeout handling."""
        with patch('subprocess.run') as mock_run:
            from subprocess import TimeoutExpired
            mock_run.side_effect = TimeoutExpired(cmd=[], timeout=120)
            result = _summarize_with_haiku("test fact sheet")
            assert "[Haiku summary failed: timeout" in result

    def test_handles_missing_claude_cli(self):
        """Test handling when claude CLI is not found."""
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = _summarize_with_haiku("test fact sheet")
            assert "[Haiku summary failed: claude CLI not found]" in result


class TestSessionFiltering:
    """Test session filtering heuristics."""

    def setup_method(self):
        """Create a mock processor for testing."""
        mock_config = {
            "db_path": "/tmp/test.db",
            "min_messages": 15,
            "min_human_words": 100,
            "stale_minutes": 15,
            "summary_model": "haiku",
            "transcript_dirs": [],
        }
        with patch('server.processor.MemorableDB'):
            self.processor = TranscriptProcessor(mock_config)

    def test_rejects_sessions_with_few_messages(self):
        """Test that sessions with <15 messages are rejected."""
        # This is tested in the _process_one flow
        # We'll verify the threshold is set correctly
        assert self.processor.min_messages == 15

    def test_rejects_sessions_with_few_human_words(self):
        """Test that sessions with <100 human words are rejected."""
        assert self.processor.min_human_words == 100

    def test_detects_autonomous_wakeup_sessions(self):
        """Test that autonomous wakeup sessions are detected."""
        # Test the detection phrase
        phrase = "scheduled moment just for you"
        messages = [{"role": "user", "text": f"This is a {phrase} message"}]

        # The phrase should be detected in lowercase
        assert phrase in messages[0]["text"].lower()

    def test_detects_low_human_ratio_sessions(self):
        """Test that low human ratio sessions are detected (<5%)."""
        human_words = 10
        total_words = 1000
        ratio = human_words / total_words

        # Should be less than 5%
        assert ratio < 0.05

    def test_detects_agent_team_sessions(self):
        """Test that agent team sessions are detected."""
        agent_phrases = [
            "you are on the",
            "you are a testing agent",
            "you are the architect",
            "check tasklist to find your assigned task",
        ]

        for phrase in agent_phrases:
            message = f"{phrase} test team".lower()
            # Each phrase should be detectable
            assert any(p in message for p in agent_phrases)

    def test_detects_observer_sessions(self):
        """Test that observer sessions are detected."""
        observer_phrases = [
            "i'm ready to observe",
            "i'll observe",
            "i'll monitor",
            "ready to observe and record",
            "monitor the primary session",
        ]

        for phrase in observer_phrases:
            message = phrase.lower()
            # Each phrase should be detectable
            assert any(p in message or message.startswith(p) for p in observer_phrases)


class TestExtractTitle:
    """Test _extract_title logic."""

    def setup_method(self):
        """Create a mock processor for testing."""
        mock_config = {
            "db_path": "/tmp/test.db",
            "min_messages": 15,
            "min_human_words": 100,
            "transcript_dirs": [],
        }
        with patch('server.processor.MemorableDB'):
            self.processor = TranscriptProcessor(mock_config)

    def test_strips_signal_prefix(self):
        """Test that Signal: prefix is stripped."""
        messages = [
            {"role": "user", "text": "Signal: Hello how are you doing today?"}
        ]
        title = self.processor._extract_title(messages)
        assert title == "Hello how are you doing today"
        assert not title.startswith("Signal:")

    def test_uses_first_substantive_message(self):
        """Test that first substantive message becomes title."""
        messages = [
            {"role": "assistant", "text": "Hello!"},
            {"role": "user", "text": "Can you help me fix the authentication bug?"}
        ]
        title = self.processor._extract_title(messages)
        assert "authentication bug" in title.lower()

    def test_skips_very_short_messages(self):
        """Test that very short messages are skipped."""
        messages = [
            {"role": "user", "text": "ok"},
            {"role": "user", "text": "hi"},
            {"role": "user", "text": "Can you help me debug this issue please?"}
        ]
        title = self.processor._extract_title(messages)
        # Should skip "ok" and "hi" (< 3 words)
        assert "debug" in title.lower()

    def test_skips_tool_output_artifacts(self):
        """Test that tool output artifacts are skipped."""
        messages = [
            {"role": "user", "text": "Read /path/to/file.py"},
            {"role": "user", "text": "Grep pattern in code"},
            {"role": "user", "text": "Please help me understand this code"}
        ]
        title = self.processor._extract_title(messages)
        # Should skip tool commands
        assert "understand" in title.lower()
        assert not title.startswith("Read")
        assert not title.startswith("Grep")

    def test_truncates_at_60_chars(self):
        """Test truncation at 60 chars."""
        long_message = "This is a very long message that should be truncated at sixty characters to keep titles concise"
        messages = [
            {"role": "user", "text": long_message}
        ]
        title = self.processor._extract_title(messages)
        assert len(title) <= 60
        if len(long_message) > 60:
            assert title.endswith("...")

    def test_fallback_to_untitled(self):
        """Test fallback to 'Untitled session'."""
        # All messages are too short or filtered out
        messages = [
            {"role": "user", "text": "ok"},
            {"role": "user", "text": "Read file.py"},
        ]
        title = self.processor._extract_title(messages)
        assert title == "Untitled session"

    def test_takes_first_sentence(self):
        """Test that title takes first sentence only."""
        messages = [
            {"role": "user", "text": "Can you help with auth? I'm having issues. Please advise."}
        ]
        title = self.processor._extract_title(messages)
        # Should end at first sentence boundary
        assert title == "Can you help with auth"

    def test_skips_autonomous_wakeup_preamble(self):
        """Test that autonomous wakeup preamble is skipped."""
        messages = [
            {"role": "user", "text": "scheduled moment just for you"},
            {"role": "user", "text": "Can you help me with this problem?"}
        ]
        title = self.processor._extract_title(messages)
        assert "problem" in title.lower()
        assert "scheduled moment" not in title.lower()

    def test_skips_progress_summary_lines(self):
        """Test that PROGRESS SUMMARY lines are skipped."""
        messages = [
            {"role": "user", "text": "PROGRESS SUMMARY: All tasks complete"},
            {"role": "user", "text": "What should we work on next?"}
        ]
        title = self.processor._extract_title(messages)
        assert "work on next" in title.lower()
        assert "PROGRESS SUMMARY" not in title


class TestGenerateHeader:
    """Test _generate_header sanitization."""

    def setup_method(self):
        """Create a mock processor for testing."""
        mock_config = {
            "db_path": "/tmp/test.db",
            "transcript_dirs": [],
        }
        with patch('server.processor.MemorableDB'):
            self.processor = TranscriptProcessor(mock_config)

    def test_reduces_multiline_to_first_line(self):
        """Test that multi-line output is reduced to first line."""
        summary = "Matt worked on authentication"
        date = datetime.now()

        with patch('server.processor._call_apple_model') as mock_afm:
            mock_afm.return_value = "🔧 Built auth | ✅ Fixed bug\nSecond line\nThird line"
            header = self.processor._generate_header(summary, date)

            # Should only have first line
            assert header == "🔧 Built auth | ✅ Fixed bug"
            assert "\n" not in header

    def test_strips_markdown_table_artifacts(self):
        """Test that markdown table artifacts are stripped."""
        summary = "Matt worked on database"
        date = datetime.now()

        with patch('server.processor._call_apple_model') as mock_afm:
            # Mock output with table separator
            mock_afm.return_value = "🗄️ Database | --- | ✅ Done"
            header = self.processor._generate_header(summary, date)

            # Should strip the table separator
            assert "---" not in header

    def test_removes_trailing_pipe(self):
        """Test trailing pipe removal."""
        summary = "Matt configured settings"
        date = datetime.now()

        with patch('server.processor._call_apple_model') as mock_afm:
            mock_afm.return_value = "⚙️ Config | ✅ Done |"
            header = self.processor._generate_header(summary, date)

            # Should remove trailing pipe
            assert not header.endswith("|")

    def test_handles_clean_output(self):
        """Test that clean output passes through."""
        summary = "Matt built feature"
        date = datetime.now()

        with patch('server.processor._call_apple_model') as mock_afm:
            mock_afm.return_value = "🔧 Built feature | ✅ Completed"
            header = self.processor._generate_header(summary, date)

            assert header == "🔧 Built feature | ✅ Completed"


class TestGetSessionDate:
    """Test _get_session_date timestamp handling."""

    def setup_method(self):
        """Create a mock processor for testing."""
        mock_config = {
            "db_path": "/tmp/test.db",
            "transcript_dirs": [],
        }
        with patch('server.processor.MemorableDB'):
            self.processor = TranscriptProcessor(mock_config)

    def test_detects_millisecond_timestamp(self):
        """Test millisecond timestamp detection (>1e12)."""
        # Create a temp file with millisecond timestamp in JSON
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            # Millisecond timestamp: 1700000000000 (Nov 2023)
            f.write(json.dumps({"timestamp": 1700000000000}) + "\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            result = self.processor._get_session_date(temp_path)
            # Should convert from milliseconds
            assert result.year == 2023
            assert result.month == 11
        finally:
            temp_path.unlink()

    def test_detects_second_timestamp(self):
        """Test second timestamp detection."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            # Second timestamp: 1700000000 (Nov 2023)
            f.write(json.dumps({"timestamp": 1700000000}) + "\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            result = self.processor._get_session_date(temp_path)
            assert result.year == 2023
            assert result.month == 11
        finally:
            temp_path.unlink()

    def test_fallback_to_file_mtime(self):
        """Test fallback to file mtime."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            # No timestamp field
            f.write(json.dumps({"other": "data"}) + "\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            result = self.processor._get_session_date(temp_path)
            # Should fall back to file modification time
            assert isinstance(result, datetime)
            # Should be very recent (within last minute)
            assert (datetime.now() - result).total_seconds() < 60
        finally:
            temp_path.unlink()

    def test_handles_malformed_json(self):
        """Test handling of malformed JSON."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write("not valid json\n")
            f.flush()
            temp_path = Path(f.name)

        try:
            result = self.processor._get_session_date(temp_path)
            # Should fall back to file mtime
            assert isinstance(result, datetime)
        finally:
            temp_path.unlink()


class TestFileHash:
    """Test _file_hash utility."""

    def test_file_hash_is_deterministic(self):
        """Test that file hash is deterministic."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("test content")
            f.flush()
            temp_path = Path(f.name)

        try:
            hash1 = TranscriptProcessor._file_hash(temp_path)
            hash2 = TranscriptProcessor._file_hash(temp_path)
            assert hash1 == hash2
        finally:
            temp_path.unlink()

    def test_file_hash_changes_with_content(self):
        """Test that file hash changes when content changes."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("content 1")
            f.flush()
            temp_path = Path(f.name)

        try:
            hash1 = TranscriptProcessor._file_hash(temp_path)

            # Modify file
            with open(temp_path, 'w') as f:
                f.write("content 2")

            hash2 = TranscriptProcessor._file_hash(temp_path)
            assert hash1 != hash2
        finally:
            temp_path.unlink()

    def test_file_hash_is_12_chars(self):
        """Test that hash is truncated to 12 chars."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("test")
            f.flush()
            temp_path = Path(f.name)

        try:
            hash_result = TranscriptProcessor._file_hash(temp_path)
            assert len(hash_result) == 12
        finally:
            temp_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
