import os
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

from harness.tools import registry
from harness.config import MODEL, STEP_BUDGET, COMPACTION_THRESHOLD, COMPACTION_KEEP_RECENT, PLAN_REMINDER_INTERVAL
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

load_dotenv()

# Decide which backend to use based on which key is set in .env.
# This is a configuration-time choice — change .env, not code.
if os.getenv("KIMI_API_KEY"):
    
    client = OpenAI(
        api_key=os.getenv("KIMI_API_KEY"),
        base_url=os.getenv("KIMI_BASE_URL"),
    )
    # K2.6 supports thinking and non-thinking modes. We disable thinking
    # to keep response shape identical to OpenAI — no reasoning_content
    # to handle, no preservation requirements in multi-turn dispatch.
    EXTRA_BODY = {"thinking": {"type": "disabled"}}
else:
    
    client = OpenAI()  # Reads OPENAI_API_KEY from environment, default base URL.
    EXTRA_BODY = {}



# The synthetic system message injected when the step budget is exceeded.
# It tells the model why it's being asked to stop and what shape its
# response should take.
BUDGET_HIT_MESSAGE = """\
You've reached the step budget for this turn (25 tool calls). Do not make
any more tool calls. Instead, respond directly to the user with:

1. What you accomplished in this turn.
2. What remains to be done.
3. What the user should ask next to continue the work.

Your response will be the final message for this turn. The user will
reply to it and you can continue from there.
"""

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



def run():
    """Run the agent's conversation loop until the user quits."""

    # Step 1: start the sandbox and wire it into the bash tool.            
    # This creates the Docker container, bind-mounts the workspace, and
    # cleans up any orphan containers from prior crashed sessions.
    sandbox = Sandbox()
    sandbox.start()
    set_sandbox(sandbox)

    # Create the Compactor once per session. Its state (last-seen token 
    # count) needs to persist across turns, so it lives outside the loop.
    compactor = Compactor(client) 

    try:
        # Load AGENTS.md and assemble the initial message list.       
        # The first system message is the harness's prompt; the second is the
        # project's accumulated memory.
        agents_md = load_agents_md()

        # The conversation history. This is the entire memory of the agent.
        # Every turn, we append to it and send the whole thing to the model.
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": agents_md}
        ]

        # Session-start plan injection — same shape as AGENTS.md loading above.
        # If workspace/plan.md exists, inject its contents as a system message
        # so the agent sees the current plan on turn 1 without asking.
        plan_content = load_plan_for_injection()
        if plan_content is not None:
            messages.append({
                "role": "system",
                "content": f"## Current plan (from workspace/plan.md)\n\n{plan_content}",
            })

            print(f"[Loaded plan.md into context: {len(plan_content):,} chars]")

        # Track turns since the last planning reminder was fired. 
        # Per-session state — must persist across all turns of the outer loop,
        # not reset each iteration. Increments on each user turn; resets to 0
        # when a reminder fires.

        turns_since_reminder = 0

        print("Agent ready. Type 'quit' or 'exit' to leave. Type /compact to force compaction.\n")

        while True:

            # Compaction check runs BEFORE reading user input. If the
            # previous turn crossed the threshold, compact now so the
            # next turn starts against a lean context.
            if compactor.should_compact():
                _run_compaction(compactor, messages)

            # Display current context size before the prompt so students 
            # can see themselves approaching the compaction threshold.
            # Suppressed on the first turn (no model call has happened
            # yet, so the count is zero and meaningless).
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
                # Manual compaction — same code path as automatic, but
                # doesn't wait for the threshold. Useful for demonstrations
                # and for the user to trigger cleanup when they know the
                # context is heavy.
                _run_compaction(compactor, messages)
                continue


            # 3. Append the user's message to the history
            messages.append({"role": "user", "content": user_input})

            # Fire the planning reminder if the interval has elapsed AND
            # plan.md has open items. Prepended as a system message so it
            # arrives ahead of the ReAct loop's first model call this turn.
            turns_since_reminder += 1
            if turns_since_reminder >= PLAN_REMINDER_INTERVAL and has_open_items():
                messages.append({"role": "system", "content": build_reminder_message()})
                
                print(f"[Planning reminder injected — {turns_since_reminder} turns since last]")
                turns_since_reminder = 0

            # Full ReAct dispatch loop — replaces the single-round dispatch   
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

                if not message.tool_calls:
                    break

                if step_count >= STEP_BUDGET:
                    messages.append({"role": "system", "content": BUDGET_HIT_MESSAGE})
                    response = client.chat.completions.create(
                        model=MODEL,
                        messages=messages,
                        tools=registry.get_schemas(),
                        tool_choice="none",
                    )

                    if response.usage:
                        compactor.record_token_usage(response.usage.prompt_tokens)
                    
                    message = response.choices[0].message
                    break

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

            # After the loop: `message.content` should have real text. If it
            # doesn't, something unexpected happened (rare API edge case or a
            # bug in the loop termination logic). Raise loudly rather than
            # silently substituting a placeholder — silent fallbacks hide real
            # problems and were exactly the 3.3 None-content workaround we're
            # now removing.
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
        # Step 2: tear down the sandbox no matter how run() exits.
        # This runs on normal exit, on exception, on user Ctrl-C — but
        # NOT on hard crashes (SIGKILL, power loss). The orphan cleanup
        # in Sandbox.start() handles those on the next session start.
        print("Stopping sandbox...")
        sandbox.stop()
        print("Sandbox stopped.")


if __name__ == "__main__":
    run()