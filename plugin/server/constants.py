"""Shared constants for Memorable backend."""

# Tools to skip when extracting action summaries and observations
SKIP_TOOLS = {
    "TodoWrite", "AskUserQuestion", "ListMcpResourcesTool",
    "ToolSearch", "EnterPlanMode", "ExitPlanMode",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "SendMessage", "TeamCreate", "TeamDelete",
}
