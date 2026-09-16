"""Subagent spawning: the parent agent delegates independent subtasks
to child agents with isolated context, shared sandbox, and a subset
of tools.

Each subagent runs its own ReAct loop against the shared workspace,
produces a summary string, and terminates. The parent gets the summary
as a tool result — never sees the subagent's tool calls or intermediate
reasoning.

Subagents deliberately lack: spawn_subagent (no recursion),
update_plan (parent owns the plan), remember (parent owns memory).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# Load .env before any module-level OpenAI() instantiation. This module
# is imported by harness/tools/__init__.py before agent.py's own
# load_dotenv() runs — so we can't rely on the entrypoint having loaded
# env for us. Idempotent: safe to call even if agent.py already loaded it.
load_dotenv()

from harness.config import (
    MODEL,
    SUBAGENT_STEP_BUDGET,
    SUBAGENT_DEFAULT_TOOLS,
    SUBAGENT_LOG_DIR,
)
from harness.tools import registry
from harness.tools.registry import tool

# Loaded once at module import — same pattern as the parent's SYSTEM_PROMPT.
SUBAGENT_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "subagent.txt"
).read_text()

# The synthetic system message injected when a subagent hits its step budget.
# Same shape as the parent's BUDGET_HIT_MESSAGE but scoped to worker context.
SUBAGENT_BUDGET_HIT_MESSAGE = """\
You've reached the step budget for this subagent task ({budget} tool
calls). Do not make any more tool calls. Instead, produce your summary
now: describe what you accomplished, what files you touched, and what
remains incomplete. Your summary will be returned to the parent agent.
"""


def _now_iso() -> str:
    """UTC timestamp in ISO-8601 for log entries and filenames."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _slugify(text: str, max_len: int = 30) -> str:
    """Turn arbitrary task text into a filesystem-safe slug."""
    # Keep only alphanumerics and spaces; collapse whitespace to hyphens.
    safe = "".join(c if c.isalnum() or c.isspace() else " " for c in text)
    safe = "-".join(safe.split())
    return safe[:max_len].lower() or "task"


def _write_execution_log(task: str, messages: list) -> Path:
    """Write the subagent's full messages list to .harness/subagents/{...}.jsonl.

    Returns the path for inclusion in the parent-visible summary — students
    can inspect the log to see exactly what the subagent did.
    """
    SUBAGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_now_iso()}_{_slugify(task)}.jsonl"
    path = SUBAGENT_LOG_DIR / filename

    with path.open("w") as f:
        for msg in messages:
            # Serialize each message. Handle both dict and pydantic-object
            # shapes (same defensive pattern as the Compactor from 7.2).
            if isinstance(msg, dict):
                f.write(json.dumps(msg) + "\n")
            else:
                # Pydantic model — use .model_dump() to get a plain dict.
                f.write(json.dumps(msg.model_dump()) + "\n")

    return path


def _get_subagent_tool_schemas(tool_names: list[str]) -> list[dict]:
    """Filter the full registry down to only the specified tool names.

    The registry has all tools registered; subagents get to see only
    the subset we expose. This is the mechanism that prevents subagents
    from calling spawn_subagent, update_plan, or remember — those tools
    exist but simply aren't in the subagent's schemas list.
    """
    all_schemas = registry.get_schemas()
    return [s for s in all_schemas if s["function"]["name"] in tool_names]


def _run_subagent_react_loop(
    client: OpenAI,
    messages: list,
    tool_schemas: list[dict],
    tool_names: set[str],
) -> str:
    """Run the ReAct loop for a subagent. Returns the final summary text.

    Similar to the parent's _run_react_loop from 8.3, but:
    - Uses SUBAGENT_STEP_BUDGET (smaller cap)
    - Only dispatches tools in tool_names (subagent's restricted set)
    - Returns the assistant's final text directly, not the message object
    """
    step_count = 0
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tool_schemas,
        )
        message = response.choices[0].message

        # Polite stop — subagent has produced its summary.
        if not message.tool_calls:
            summary = message.content or ""
            # Append the summary to messages so the execution log
            # contains the complete trajectory (including the final
            # response the parent will see).
            messages.append({"role": "assistant", "content": summary})
            return summary

        # Step budget hit — force a summary and return it.
        if step_count >= SUBAGENT_STEP_BUDGET:
            messages.append({
                "role": "system",
                "content": SUBAGENT_BUDGET_HIT_MESSAGE.format(
                    budget=SUBAGENT_STEP_BUDGET
                ),
            })
            summary_response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tool_schemas,
                tool_choice="none",
            )
            summary = summary_response.choices[0].message.content or ""
            # Append the summary so the log contains the complete trajectory.
            messages.append({"role": "assistant", "content": summary})
            return summary

        # Continue: append the message, dispatch each tool call, append results.
        messages.append(message)
        for call in message.tool_calls:
            # Extra guard: even though tool_schemas is filtered, defense in
            # depth — if the model somehow calls a tool not in tool_names,
            # return an error rather than dispatching it. This prevents any
            # bug in the schema filter from allowing recursion.
            if call.function.name not in tool_names:
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": (
                        f"[error] Tool '{call.function.name}' is not "
                        f"available to subagents. Available tools: "
                        f"{sorted(tool_names)}"
                    ),
                })
                continue

            arguments = json.loads(call.function.arguments)
            result = registry.dispatch(call.function.name, arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })
        step_count += 1


# Client is instantiated fresh per spawn_subagent call. Sharing the
# parent's client would work too, but instantiation is cheap and this
# keeps the subagent's execution self-contained.
_client = OpenAI()


@tool
def spawn_subagent(task: str, tools: list[str] | None = None) -> str:
    """Delegate a scoped subtask to a subagent with isolated context.

    The subagent runs in the same sandbox and workspace as the parent
    (so files, git state, and environment are shared) but with its own
    conversation context. The subagent completes the task and returns
    a summary string, which becomes this tool's result.

    Use this for plan tasks that are genuinely independent — no shared
    state with other subtasks, no ordering constraints. For tasks that
    need integrated context (e.g., "given what you learned from the
    previous task, now do X"), execute them yourself instead.

    Args:
        task: A scoped description of what the subagent should do.
            This becomes the subagent's initial user message and its
            entire knowledge of the goal — be specific and complete.
        tools: Optional list of tool names to expose to the subagent.
            Defaults to the standard working subset (filesystem, git,
            bash, web_search, recall). Override when a task needs a
            narrower set — e.g., ["web_search", "recall"] for pure
            research subtasks.

    Returns:
        The subagent's final summary as a plain string.
    """
    # Resolve the tool list — either the caller's override or the default.
    tool_names = tools if tools is not None else SUBAGENT_DEFAULT_TOOLS
    tool_schemas = _get_subagent_tool_schemas(tool_names)

    # Bootstrap the subagent's messages: subagent-specific system prompt +
    # the task as the initial user message. No AGENTS.md, no plan.md —
    # the subagent is scoped to its one task.
    messages = [
        {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    print(f"[Subagent spawning: {_slugify(task, max_len=60)}]")

    try:
        # Run the subagent's ReAct loop to completion.
        summary = _run_subagent_react_loop(
            _client, messages, tool_schemas, set(tool_names)
        )

        # Persist the execution log for post-hoc inspection.
        log_path = _write_execution_log(task, messages)

        step_count = sum(
            1 for m in messages
            if (isinstance(m, dict) and m.get("role") == "tool")
               or (not isinstance(m, dict) and getattr(m, "role", None) == "tool")
        )
        print(f"[Subagent completed: {step_count} tool calls, log at {log_path}]")

        return summary

    except Exception as e:
        # Any exception during subagent execution — API error, tool
        # dispatch failure, etc. — surfaces to the parent as an error
        # string rather than crashing the parent's session.
        print(f"[Subagent failed: {e}]")
        return f"[error] Subagent failed with exception: {e}"