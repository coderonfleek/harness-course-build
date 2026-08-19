import os
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

from harness.tools import registry
from harness.memory import load_agents_md
from harness.config import MODEL, STEP_BUDGET

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

def run():
    """Run the agent's conversation loop until the user quits."""

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


if __name__ == "__main__":
    run()