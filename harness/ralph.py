"""Ralph loop: detect polite agent stops mid-task, evaluate whether the
goal is met, force continuation in a fresh context if work remains.

Two-stage evaluation:
1. Cheap check — does plan.md have open items? If yes, work remains.
2. Expensive check — semantic evaluation via model call. Catches the
   "plan looks done but the goal isn't" case.

When Ralph decides to force continuation, it doesn't just inject a
message — it clears the context entirely, re-runs session-start
bootstrap (which loads plan.md and AGENTS.md), and injects a synthetic
'continue' user message. The persistent state survives; the polluted
context does not.
"""

from harness.config import (
    RALPH_MAX_CONTINUATIONS,
    RALPH_EVAL_MODEL,
    RALPH_EVAL_PROMPT,
)
from harness.planning import has_open_items, load_plan_for_injection


def _summarize_recent(messages: list, max_chars: int = 2000) -> str:
    """Produce a compact summary of the last few turns for Ralph's semantic check.

    Not a full compaction — just enough context for the evaluator to judge
    whether the goal was met. Takes the last N assistant messages and
    truncates.
    """
    # Grab the last few assistant messages — they contain the agent's
    # claimed work. Skip tool results (too verbose for this purpose).
    assistant_texts = []
    for msg in reversed(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if role == "assistant" and content:
            assistant_texts.append(content)
            if len(assistant_texts) >= 3:
                break

    combined = "\n---\n".join(reversed(assistant_texts))
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n[truncated]"
    return combined or "(no recent assistant messages)"


def _semantic_check(client, goal: str, messages: list) -> tuple[bool, str]:
    """Ask the model whether the goal has been met.

    Returns (is_done, reason). Reason is for logging/debugging.
    """
    plan = load_plan_for_injection() or "(no plan)"
    recent_summary = _summarize_recent(messages)

    prompt = RALPH_EVAL_PROMPT.format(
        goal=goal,
        plan=plan,
        recent_summary=recent_summary,
    )

    response = client.chat.completions.create(
        model=RALPH_EVAL_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )

    answer = (response.choices[0].message.content or "").strip()

    if answer.startswith("DONE:"):
        return True, answer[len("DONE:"):].strip()
    if answer.startswith("NOT_DONE:"):
        return False, answer[len("NOT_DONE:"):].strip()

    # Unexpected format — treat as done to avoid looping on parse failures.
    # Log the anomaly so it can be diagnosed.
    print(f"[Ralph] Unexpected eval response format: {answer[:200]!r}. Treating as DONE.")
    return True, "eval response malformed"


def is_goal_met(client, goal: str, messages: list) -> tuple[bool, str]:
    """Two-stage evaluation: plan-check first, semantic check second.

    Returns (is_done, reason). Reason is for logging/debugging.
    """
    # Stage 1: cheap check. If plan.md has open items, we're definitely
    # not done — no need for the model call.
    if has_open_items():
        return False, "plan.md has open items"

    # Stage 2: expensive check. Plan claims completion; verify semantically.
    return _semantic_check(client, goal, messages)


def should_fire(goal: str | None) -> bool:
    """Ralph fires only when explicitly invoked via /ralph AND when a
    substantive user goal has been captured.

    In the user-invoked design, the gate isn't plan.md existence — it's
    whether Ralph was actually asked to run. This function is called
    from the /ralph command handler in agent.py to guard against the
    edge case where the user typed /ralph with no prior task.

    The plan.md existence check happens in the command handler (which
    refuses invocation entirely if no plan exists), not here.
    """
    return goal is not None


def build_no_plan_refusal() -> str:
    """Message shown when /ralph is invoked but plan.md doesn't exist."""
    return (
        "[Ralph] No plan.md exists — invoke /ralph only after a plan has "
        "been created. Ask the agent to draw up a detailed plan first, "
        "then re-invoke."
    )


def build_no_goal_refusal() -> str:
    """Message shown when /ralph is invoked with no prior substantive input."""
    return (
        "[Ralph] No task in flight — send the agent a real task first, "
        "then invoke /ralph to have it drive the task to completion."
    )


def build_continuation_notice(iteration: int, reason: str) -> str:
    """The user-visible message printed when Ralph forces a continuation."""
    return (
        f"[Ralph continuation #{iteration}] Agent stopped but work remains. "
        f"Reason: {reason}. Restarting session with fresh context and continuing."
    )


def build_cap_hit_notice(max_continuations: int) -> str:
    """The user-visible message printed when Ralph hits the continuation cap."""
    return (
        f"[Ralph] Continuation cap ({max_continuations}) reached. Work still "
        f"appears to remain — please investigate before continuing. The agent "
        f"may be stuck in a loop it can't resolve, or the goal may be "
        f"underspecified. Check plan.md and recent output."
    )