"""Tests for shared constants module."""

import pytest
from server.constants import SKIP_TOOLS


def test_skip_tools_is_set():
    """Verify SKIP_TOOLS is a set, not a list."""
    assert isinstance(SKIP_TOOLS, set), "SKIP_TOOLS should be a set for O(1) lookups"


def test_skip_tools_contains_expected_entries():
    """Verify SKIP_TOOLS contains expected tool names."""
    expected_tools = {
        "TodoWrite",
        "AskUserQuestion",
        "ListMcpResourcesTool",
        "ToolSearch",
        "EnterPlanMode",
        "ExitPlanMode",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "SendMessage",
        "TeamCreate",
        "TeamDelete",
    }

    assert expected_tools.issubset(SKIP_TOOLS), \
        f"SKIP_TOOLS missing expected entries. Missing: {expected_tools - SKIP_TOOLS}"


def test_skip_tools_consistency_with_notes():
    """Verify that importing SKIP_TOOLS from notes.py gives same set."""
    from server.notes import _SKIP_TOOLS as notes_skip_tools
    from server.constants import SKIP_TOOLS as constants_skip_tools

    assert notes_skip_tools == constants_skip_tools, \
        "SKIP_TOOLS should be consistent between notes.py and constants.py"


def test_skip_tools_consistency_with_observer():
    """Verify that importing SKIP_TOOLS from observer.py gives same set."""
    try:
        from server.observer import SKIP_TOOLS as observer_skip_tools
        from server.constants import SKIP_TOOLS as constants_skip_tools

        assert observer_skip_tools == constants_skip_tools, \
            "SKIP_TOOLS should be consistent between observer.py and constants.py"
    except ImportError:
        # observer.py might not exist or might not import SKIP_TOOLS
        pytest.skip("observer.py does not import SKIP_TOOLS")


def test_skip_tools_not_empty():
    """Verify SKIP_TOOLS is not empty."""
    assert len(SKIP_TOOLS) > 0, "SKIP_TOOLS should contain at least one tool name"


def test_skip_tools_entries_are_strings():
    """Verify all entries in SKIP_TOOLS are strings."""
    for tool in SKIP_TOOLS:
        assert isinstance(tool, str), f"Tool name {tool!r} should be a string"
        assert len(tool) > 0, f"Tool name should not be empty string"
