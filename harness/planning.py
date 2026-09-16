"""Planning support: a persistent plan.md file that the agent maintains
via the dedicated update_plan tool.

The harness owns three things:
1. The plan.md format (strict markdown checkbox list)
2. The session-start injection (loads plan.md into context on turn 1)
3. Periodic update reminders (nudges the agent every N turns)

The model owns two things:
1. When to create the plan (decides a task warrants planning)
2. What tasks belong in the plan (decomposition is a model responsibility)

"""

from datetime import datetime, timezone
from pathlib import Path

from harness.config import (
    PLAN_FILENAME,
    PLAN_REMINDER_INTERVAL,
    PLAN_INJECTION_MAX_CHARS,
    WORKSPACE,
)
from harness.tools.registry import tool

# The three allowed status values. Kept as a set for O(1) validation
# and as a canonical source of truth for the status vocabulary.
VALID_STATUSES = {"todo", "done", "blocked"}

# Mapping from status to the checkbox representation. Only "done"
# gets the [x]; everything else is [ ] with the status noted inline.
_CHECKBOX = {
    "todo": "[ ]",
    "done": "[x]",
    "blocked": "[ ]",
}


def _plan_path() -> Path:
    """Absolute path to workspace/plan.md."""
    return WORKSPACE / PLAN_FILENAME


def _now_iso() -> str:
    """UTC timestamp in ISO-8601 for log entries."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_plan(goal: str = "New task") -> str:
    """The initial plan.md content when the file is created for the first time."""
    return (
        f"# Plan: {goal}\n\n"
        f"## Status: in progress\n\n"
        f"## Tasks\n\n"
        f"## Log\n"
        f"- {_now_iso()} — Plan created\n"
    )


def _parse_tasks(plan_text: str) -> list[dict]:
    """Extract the task list from plan.md.

    Returns a list of {text, status, notes} dicts, one per task line.
    Handles the strict format the harness produces; malformed lines
    are skipped (defensive — if the file was manually edited into an
    invalid state, we don't crash).
    """
    tasks = []
    in_tasks_section = False

    for line in plan_text.splitlines():
        stripped = line.strip()

        # Section boundaries.
        if stripped.startswith("## Tasks"):
            in_tasks_section = True
            continue
        if stripped.startswith("## ") and in_tasks_section:
            in_tasks_section = False
            continue

        if not in_tasks_section:
            continue

        # Task line — either "- [x] text" (done) or "- [ ] text" (open).
        if stripped.startswith("- [x] "):
            tasks.append({"text": stripped[6:], "status": "done", "notes": None})
        elif stripped.startswith("- [ ] "):
            # Check for a "(blocked)" marker inline. Simple heuristic:
            # a bracketed status suffix marks non-done open items.
            text = stripped[6:]
            status = "blocked" if text.endswith(" (blocked)") else "todo"
            if status == "blocked":
                text = text[: -len(" (blocked)")]
            tasks.append({"text": text, "status": status, "notes": None})
        elif stripped.startswith("- Notes: ") and tasks:
            # Notes attach to the most recent task.
            tasks[-1]["notes"] = stripped[9:]

    return tasks


def _render_plan(goal: str, tasks: list[dict], log_entries: list[str]) -> str:
    """Render the plan.md file from its components.

    Produces the strict format the harness parses. Every write goes
    through here — the format is guaranteed correct because the harness
    controls the serialization.
    """
    lines = [f"# Plan: {goal}", "", "## Status: in progress", "", "## Tasks", ""]

    for task in tasks:
        marker = _CHECKBOX[task["status"]]
        suffix = " (blocked)" if task["status"] == "blocked" else ""
        lines.append(f"- {marker} {task['text']}{suffix}")
        if task.get("notes"):
            lines.append(f"  - Notes: {task['notes']}")

    lines.extend(["", "## Log"])
    for entry in log_entries:
        lines.append(f"- {entry}")

    return "\n".join(lines) + "\n"


def _extract_goal(plan_text: str) -> str:
    """Extract the goal from the plan's first heading. Falls back to 'New task'."""
    for line in plan_text.splitlines():
        if line.startswith("# Plan: "):
            return line[len("# Plan: "):].strip()
    return "New task"


def _extract_log(plan_text: str) -> list[str]:
    """Extract log entries from the plan. Falls back to empty list."""
    entries = []
    in_log = False
    for line in plan_text.splitlines():
        if line.strip() == "## Log":
            in_log = True
            continue
        if in_log and line.strip().startswith("- "):
            entries.append(line.strip()[2:])
    return entries


def has_open_items() -> bool:
    """True if plan.md exists and has at least one non-done task."""
    path = _plan_path()
    if not path.exists():
        return False
    tasks = _parse_tasks(path.read_text())
    return any(t["status"] != "done" for t in tasks)


def load_plan_for_injection() -> str | None:
    """Return the plan.md content for session-start injection, or None if no plan.

    Caps at PLAN_INJECTION_MAX_CHARS with a truncation marker if the file is
    pathologically large. Real plans are small; this is a guard, not a common path.
    """
    path = _plan_path()
    if not path.exists():
        return None

    content = path.read_text()
    if len(content) > PLAN_INJECTION_MAX_CHARS:
        truncated = content[:PLAN_INJECTION_MAX_CHARS]
        return truncated + f"\n\n[Truncated at {PLAN_INJECTION_MAX_CHARS} chars]"
    return content


def build_reminder_message() -> str:
    """The system message the harness injects when the reminder fires."""
    return (
        "[Planning reminder] You have open items in plan.md. "
        "If you've made progress on any of them, call `update_plan` now "
        "before continuing."
    )


@tool
def update_plan(task: str, status: str, notes: str | None = None) -> str:
    """Update the plan.md file for the current session.

    Creates plan.md if it doesn't exist. Adds the task if it isn't already
    in the plan. Updates status if it is. Optionally attaches notes.

    Use this for EVERY state change on plan.md — creating a new task,
    marking one done, marking one blocked. Do NOT use write() on plan.md
    directly; that call will be refused.

    Args:
        task: The task text. If a matching task already exists in the plan,
            its status is updated. If not, the task is added with the given
            status.
        status: One of "todo", "done", or "blocked".
        notes: Optional notes to attach to the task. Overwrites any previous
            notes on the same task.

    Returns:
        A short confirmation string.
    """
    # Step 1: validate the status. Return a clear error the model can recover from.
    if status not in VALID_STATUSES:
        return (
            f"error: invalid status {status!r}. "
            f"Valid statuses: {sorted(VALID_STATUSES)}"
        )

    path = _plan_path()

    # Step 2: load the existing plan or bootstrap a new one.
    if path.exists():
        existing_text = path.read_text()
        goal = _extract_goal(existing_text)
        tasks = _parse_tasks(existing_text)
        log_entries = _extract_log(existing_text)
    else:
        # First call — bootstrap the plan. The task text becomes the initial goal
        # if no better goal is available. This is a compromise: we don't force
        # the model to call a separate create_plan first, but we get a sensible
        # default when it doesn't.
        goal = task
        tasks = []
        log_entries = [f"{_now_iso()} — Plan created"]

    # Step 3: find or add the task. Case-sensitive exact match — same task
    # text with different capitalization is treated as different tasks
    # (avoids fuzzy-match surprise).
    matching = next((t for t in tasks if t["text"] == task), None)

    if matching is not None:
        prev_status = matching["status"]
        matching["status"] = status
        if notes is not None:
            matching["notes"] = notes
        log_entries.insert(0, f"{_now_iso()} — Task {task!r} moved {prev_status} → {status}")
        action = f"updated task {task!r} to {status}"
    else:
        tasks.append({"text": task, "status": status, "notes": notes})
        log_entries.insert(0, f"{_now_iso()} — Task {task!r} added as {status}")
        action = f"added task {task!r} as {status}"

    # Step 4: render and write. The render is deterministic — every plan
    # written by update_plan is in the harness's canonical format.
    path.write_text(_render_plan(goal, tasks, log_entries))

    return f"[update_plan] {action}"