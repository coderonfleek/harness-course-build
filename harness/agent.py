import os
import json
from dotenv import load_dotenv
from openai import OpenAI

from pathlib import Path

from harness.tools import registry

# <---- MODIFIED (added RALPH_MAX_CONTINUATIONS, RALPH_CONTINUATION_PROMPT)
from harness.config import (                                              
    MODEL,
    STEP_BUDGET,
    COMPACTION_THRESHOLD,
    COMPACTION_KEEP_RECENT,
    PLAN_REMINDER_INTERVAL,
    RALPH_MAX_CONTINUATIONS,
    RALPH_CONTINUATION_PROMPT,
)

from harness.sandbox import Sandbox
from harness.tools.bash import set_sandbox

from harness.memory import (
    load_agents_md,
    save_agents_md,
    validate_agents_md_structure,
)

from harness.context import Compactor

from harness.planning import (
    load_plan_for_injection,
    has_open_items,
    build_reminder_message,
)

# <---- NEW IMPORT BLOCK
from harness.ralph import (                                               
    should_fire as ralph_should_fire,
    is_goal_met,
    build_continuation_notice,
    build_cap_hit_notice,
    build_no_plan_refusal,
    build_no_goal_refusal,
)

# Load OPENAI_API_KEY from .env into the environment
load_dotenv()

# The system prompt — read by the model on every turn, because the full
# history (including this) is resent on every API call. Edit deliberately:
# every sentence here affects every interaction.
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.txt").read_text()

# Consolidation prompt loaded from file, like the system prompt.
SESSION_END_MEMORY_PROMPT = (
    Path(__file__).parent / "prompts" / "session_end_memory.txt"
).read_text()


def _consolidate_memory(messages: list, client) -> None:
    """Run the end-of-session memory consolidation step.

    Sends the current AGENTS.md + full session history + a consolidation
    prompt to the model. Expects a full AGENTS.md rewrite back. Validates
    the structure before writing. On failure, logs and keeps the old file.
    """
    # (from 6.2, unchanged)
    print("Consolidating memory...")

    try:
        # Step 1: gather the inputs — current AGENTS.md and the full history.
        current_agents_md = load_agents_md()

        # Step 2: build the consolidation payload. We reuse the session's
        # message history but append a fresh system message with the
        # consolidation prompt + the current AGENTS.md. The model sees
        # everything it needs to produce the rewrite.
        consolidation_context = (
            f"{SESSION_END_MEMORY_PROMPT}\n\n"
            f"=== Current AGENTS.md ===\n{current_agents_md}\n"
        )
        consolidation_messages = messages + [
            {"role": "system", "content": consolidation_context},
        ]

        # Step 3: one model call, no tools. tool_choice="none" forces text
        # output — we want the file content, not tool invocations.
        response = client.chat.completions.create(
            model=MODEL,
            messages=consolidation_messages,
        )
        proposed = response.choices[0].message.content

        if not proposed:
            print("Memory consolidation returned empty content. Keeping current AGENTS.md.")
            return

        # Step 4: validate structure. Reject a malformed return without
        # touching the file.
        if not validate_agents_md_structure(proposed):
            print(
                "Memory consolidation returned malformed structure "
                "(missing section headers). Keeping current AGENTS.md."
            )
            return

        # Step 5: atomic write. The old file is replaced by the new one
        # in a single filesystem operation.
        save_agents_md(proposed)
        print("Memory updated.")

    except Exception as e:
        # If anything goes wrong during consolidation — API error, timeout,
        # unexpected exception — we log and return. The old AGENTS.md stays
        # intact. Consolidation is a nice-to-have; sandbox teardown is
        # non-negotiable.
        print(f"Memory consolidation failed: {e}. Keeping current AGENTS.md.")


def _run_compaction(compactor: Compactor, messages: list[dict]) -> None:
    # (from 7.2, unchanged)
    before = compactor.get_last_token_count()
    new_messages = compactor.compact(messages)

    if new_messages is None:
        # No-op: not enough conversation to compact.
        print(
            "[Compaction skipped: not enough older history to summarize "
            f"(need more than {COMPACTION_KEEP_RECENT} user turns).]"
        )
        return

    messages.clear()
    messages.extend(new_messages)

    approximate_after = compactor.approximate_char_count(messages)
    print(
        f"[Context compacted at {before:,} tokens → ~{approximate_after:,} tokens. "
        f"Full history: .harness/context_log.jsonl]"
    )


# The synthetic system message injected when the step budget is exceeded.
# It tells the model why it's being asked to stop and what shape its
# response should take.
# (from 4.x, unchanged)
BUDGET_HIT_MESSAGE = """\
You've reached the step budget for this turn (25 tool calls). Do not make
any more tool calls. Instead, respond directly to the user with:

1. What you accomplished in this turn.
2. What remains to be done.
3. What the user should ask next to continue the work.

Your response will be the final message for this turn. The user will
reply to it and you can continue from there.
"""

# One client, reused for every call
client = OpenAI()

# <---- NEW HELPER
def _build_initial_messages() -> list[dict]:                              
    """Build the messages list for a fresh session.

    Contains: system prompt, AGENTS.md, and (if it exists) plan.md.
    Called once at session start by run(), and once per forced
    continuation by the /ralph command handler.

    Extracted so the two callers share exactly the same bootstrap —
    no drift between "start of session" and "Ralph reset."
    """
    agents_md = load_agents_md()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": agents_md},
    ]

    # Session-start plan injection (from 8.2).
    plan_content = load_plan_for_injection()
    if plan_content is not None:
        messages.append({
            "role": "system",
            "content": f"## Current plan (from workspace/plan.md)\n\n{plan_content}",
        })
        print(f"[Loaded plan.md into context: {len(plan_content):,} chars]")

    return messages

# <---- NEW HELPER
def _run_react_loop(client, messages: list, compactor: Compactor):        
    """Run the ReAct loop until a polite stop or step budget hit.

    Returns the final assistant message. Does NOT print the message —
    the caller does that, so the caller controls when 'agent > ...'
    appears relative to Ralph decisions.

    Extracted from the inline loop that lived in run() so /ralph can
    invoke it repeatedly across forced continuations.

    Terminates when either:
      (a) the model emits a text response with no tool calls, or
      (b) STEP_BUDGET rounds pass and the harness forces a summary.
    """
    step_count = 0
    while True:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=registry.get_schemas(),
        )
        if response.usage:
            compactor.record_token_usage(response.usage.prompt_tokens)

        message = response.choices[0].message

        # Polite stop: no tool calls, model produced text.
        if not message.tool_calls:
            return message

        # Step budget hit: force a summary and return the summary message.
        if step_count >= STEP_BUDGET:
            messages.append({"role": "system", "content": BUDGET_HIT_MESSAGE})
            summary_response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=registry.get_schemas(),
                tool_choice="none",
            )
            if summary_response.usage:
                compactor.record_token_usage(summary_response.usage.prompt_tokens)
            return summary_response.choices[0].message

        # Continue: append the tool_calls message, dispatch each call, append results.
        messages.append(message)
        for call in message.tool_calls:
            arguments = json.loads(call.function.arguments)
            result = registry.dispatch(call.function.name, arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })
        step_count += 1

# <---- NEW HELPER
def _run_ralph(client, messages, compactor, goal, continuation_prompt):   
    """Run Ralph until the goal is met or the continuation cap is hit.

    Called by the /ralph command handler. Each iteration:
    1. Runs the ReAct loop
    2. Prints the assistant response
    3. Evaluates whether the goal is met (plan-check + semantic check)
    4. If not met, rebuilds context from scratch and injects the
       continuation prompt as a fresh user message

    Guarantees clean handoff back to the caller — the caller can
    resume the normal REPL loop without any leftover Ralph state.
    """
    continuation_count = 0
    while True:
        # Run one full ReAct loop cycle.
        message = _run_react_loop(client, messages, compactor)

        # Guard against empty content (from 3.3, unchanged discipline).
        if not message.content:
            raise RuntimeError(
                "Loop terminated but message.content is empty. "
                "This shouldn't happen — check the API response and the "
                "termination logic."
            )

        assistant_text = message.content
        messages.append({"role": "assistant", "content": assistant_text})
        print(f"\nagent > {assistant_text}\n")

        # Evaluate whether the goal is met.
        is_done, reason = is_goal_met(client, goal, messages)
        if is_done:
            print(f"[Ralph] Goal met: {reason}")
            return

        # Not done — force continuation, subject to the cap.
        continuation_count += 1
        if continuation_count > RALPH_MAX_CONTINUATIONS:
            print(build_cap_hit_notice(RALPH_MAX_CONTINUATIONS))
            return

        print(build_continuation_notice(continuation_count, reason))

        # Rebuild context from scratch — attention reset.
        # Persistent state (plan.md, AGENTS.md) survives via
        # _build_initial_messages. Inject the continuation prompt
        # (either the default or the user's override) as a user message.
        messages.clear()
        messages.extend(_build_initial_messages())
        messages.append({"role": "user", "content": continuation_prompt})


def run():
    """Run the agent's conversation loop until the user quits."""

    # Step 1: start the sandbox and wire it into the bash tool.
    # (from 5.x, unchanged)
    sandbox = Sandbox()
    sandbox.start()
    set_sandbox(sandbox)

    # Create the Compactor once per session.
    # (from 7.2, unchanged)
    compactor = Compactor(client)

    try:
        messages = _build_initial_messages()

        # Track turns since the last planning reminder was fired.
        # (from 8.2, unchanged)
        turns_since_reminder = 0
				
		# <---- NEW SESSION STATE
				
        # Track the most recent substantive user input.                   
        # Used as the goal when /ralph is invoked. Non-command user
        # messages update this; command messages (/ralph, /compact,
        # quit, exit) do not. Starts as None — /ralph is refused
        # until a real task has been sent.
        last_substantive_input: str | None = None

        print(
            "Agent ready. Type 'quit' or 'exit' to leave. "
            "Type /compact to force compaction. "
            "Type /ralph [instruction] to hand execution to the Ralph loop.\n"
        )

        while True:
            # Compaction check runs BEFORE reading user input.
            # (from 7.2, unchanged)
            if compactor.should_compact():
                _run_compaction(compactor, messages)

            # Display current context size before the prompt.
            # (from 7.2, unchanged)
            current_tokens = compactor.get_last_token_count()
            if current_tokens > 0:
                print(f"[Context: {current_tokens:,} / {COMPACTION_THRESHOLD:,} tokens]")

            # 1. Get input from the user
            user_input = input("you > ").strip()

            # 2. Allow the user to leave cleanly
            if user_input in {"quit", "exit"}:
                _consolidate_memory(messages, client)
                print("Goodbye.")
                break

            # Skip empty lines without making a model call
            if not user_input:
                continue

            if user_input == "/compact":
                # Manual compaction — same code path as automatic.
                # (from 7.2, unchanged)
                _run_compaction(compactor, messages)
                continue
						
			# <---- NEW BLOCK
						
            # /ralph command handling.                                    
            # Syntax:
            #   /ralph               -> use default continuation prompt
            #   /ralph <instruction> -> use <instruction> as continuation prompt
            #
            # Refuses in two cases:
            #   (a) no plan.md exists (need a plan for the fast-path check)
            #   (b) no substantive input has been sent (no goal to evaluate against)
            if user_input == "/ralph" or user_input.startswith("/ralph "):
                # Guard 1: refuse if no plan exists.
                if load_plan_for_injection() is None:
                    print(build_no_plan_refusal())
                    continue

                # Guard 2: refuse if no substantive input to serve as goal.
                if not ralph_should_fire(last_substantive_input):
                    print(build_no_goal_refusal())
                    continue

                # Parse optional per-invocation continuation prompt.
                if user_input == "/ralph":
                    continuation_prompt = RALPH_CONTINUATION_PROMPT
                else:
                    continuation_prompt = user_input[len("/ralph "):].strip()
                    if not continuation_prompt:
                        continuation_prompt = RALPH_CONTINUATION_PROMPT

                # Hand execution to Ralph. This runs the ReAct loop
                # repeatedly with forced continuations until the goal
                # is met or the cap is hit. Uses last_substantive_input
                # as the goal — the plan.md contents are read fresh
                # inside is_goal_met each iteration.
                _run_ralph(
                    client,
                    messages,
                    compactor,
                    goal=last_substantive_input,
                    continuation_prompt=continuation_prompt,
                )
                continue
						
			# <---- MODIFIED (added last_substantive_input tracking)
            # 3. Regular user input (not a command).                      
            # Update last_substantive_input so /ralph can use it as goal.
            last_substantive_input = user_input
            messages.append({"role": "user", "content": user_input})

            # Fire the planning reminder if the interval has elapsed AND
            # plan.md has open items.
            # (from 8.2, unchanged)
            turns_since_reminder += 1
            if turns_since_reminder >= PLAN_REMINDER_INTERVAL and has_open_items():
                messages.append({"role": "system", "content": build_reminder_message()})
                print(f"[Planning reminder injected — {turns_since_reminder} turns since last]")
                turns_since_reminder = 0
						
			# <---- MODIFIED (was ~30 lines of inline ReAct code; now delegates to _run_react_loop)
            # Run one ReAct loop cycle for this turn.                     
            # Behavior is unchanged from 8.2 — same tool dispatch, same
            # step budget handling, same token accounting. The loop just
            # lives in a helper now so /ralph can invoke it repeatedly.
            message = _run_react_loop(client, messages, compactor)

            # Guard against empty content (from 3.3, unchanged discipline).
            if not message.content:
                raise RuntimeError(
                    "Loop terminated but message.content is empty. "
                    "This shouldn't happen — check the API response and the "
                    "termination logic."
                )

            assistant_text = message.content
            messages.append({"role": "assistant", "content": assistant_text})
            print(f"\nagent > {assistant_text}\n")

    finally:
        # Tear down the sandbox no matter how run() exits.
        # (from 5.x, unchanged)
        print("Stopping sandbox...")
        sandbox.stop()
        print("Sandbox stopped.")


if __name__ == "__main__":
    run()