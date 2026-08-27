import os
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

from harness.tools import registry
from harness.config import MODEL, STEP_BUDGET
from harness.sandbox import Sandbox
from harness.tools.bash import set_sandbox

from harness.memory import ( 
    load_agents_md,
    save_agents_md,
    validate_agents_md_structure,
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



def run():
    """Run the agent's conversation loop until the user quits."""

    # Step 1: start the sandbox and wire it into the bash tool.            
    # This creates the Docker container, bind-mounts the workspace, and
    # cleans up any orphan containers from prior crashed sessions.
    sandbox = Sandbox()
    sandbox.start()
    set_sandbox(sandbox)

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

        print("Agent ready. Type 'quit' or 'exit' to leave.\n")

        while True:

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

            # 3. Append the user's message to the history
            messages.append({"role": "user", "content": user_input})

            # Full ReAct dispatch loop — replaces the single-round dispatch   
            step_count = 0
            while True:
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=registry.get_schemas(),
                )
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