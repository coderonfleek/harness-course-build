import os
import json
from dotenv import load_dotenv
from openai import OpenAI

from harness.tools import registry
from harness.memory import load_agents_md

load_dotenv()

# Decide which backend to use based on which key is set in .env.
# This is a configuration-time choice — change .env, not code.
if os.getenv("KIMI_API_KEY"):
    MODEL = "kimi-k2.6"
    client = OpenAI(
        api_key=os.getenv("KIMI_API_KEY"),
        base_url=os.getenv("KIMI_BASE_URL"),
    )
    # K2.6 supports thinking and non-thinking modes. We disable thinking
    # to keep response shape identical to OpenAI — no reasoning_content
    # to handle, no preservation requirements in multi-turn dispatch.
    EXTRA_BODY = {"thinking": {"type": "disabled"}}
else:
    MODEL = "gpt-4o-mini"
    client = OpenAI()  # Reads OPENAI_API_KEY from environment, default base URL.
    EXTRA_BODY = {}

# Maximum number of tool-call rounds per user turn. When hit, the harness
# forces the model to summarize what it did and hand control back to the
# user, instead of letting the loop run indefinitely.
STEP_BUDGET = 25

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

SYSTEM_PROMPT = """
You are a coding assistant running in a terminal, helping a developer with software engineering tasks.

Be concise. Prefer short, direct answers over long ones. When the user asks for code, return the code with minimal explanation unless they ask for more.

When returning code, use fenced code blocks and specify the language.

You have access to five filesystem tools — read, write, list, mkdir, delete — operating on a workspace directory. Use them whenever a task involves reading, modifying, or organizing files. Paths are relative to
the workspace root. Prefer reading and writing real files over describing them in conversation.

You also have six git tools — git_status, git_diff, git_log, git_commit, git_checkout, git_branch — for versioning your work. The workspace is
already initialized as a git repo. Use git to:
- Commit frequently. Small, focused commits are easier to roll back.
- Commit before doing anything risky (large rewrites, deleting files, restructuring). A commit before the risky step gives you a recovery point.
- Write meaningful commit messages — describe what changed and why, in the present tense (e.g., "add user authentication module").
- Branch experiments. When trying an alternative approach, create a branch first so the main line of work stays intact.

You have one more tool: `bash`. It executes shell commands in the           
workspace with full shell interpretation — pipes, redirects, and command
chaining all work. Use bash for anything the specific tools above don't
cover: running scripts (python foo.py, node foo.js), invoking system
utilities (grep, find, curl, wc, sort, awk), installing packages
(pip install ...), or exploring the environment (ls, pwd, which python).

Prefer the specific tools when they apply. If the task is to read a
file, use `read`, not `bash("cat file.md")`. If the task is to commit,
use `git_commit`, not `bash("git commit ...")`. The specific tools are
safer, faster, and clearer to trace. Reach for `bash` when the specific
tools don't cover what you need — which is often, because software work
is varied.

Bash commands see the workspace as their working directory. `cd` inside
a bash command does not persist to the next tool call; each bash
invocation starts fresh from the workspace root.

The workspace contains an `AGENTS.md` file — your durable memory across sessions. It is automatically loaded into your context at the start of
every session. Update it (using the `write` tool) when you learn something worth remembering for future sessions. Good things to write:
- Project context: what this codebase is, what it does, who uses it
- Conventions you've observed: code style, libraries, naming patterns
- Decisions that have been made and the reasoning behind them
- Gotchas: quirks, non-obvious dependencies, things that have tripped up earlier sessions
- Active tasks: what's currently being worked on (clear when complete)

When updating AGENTS.md, preserve the existing structure (the section headings). Add to the relevant section instead of replacing the whole file. If the section starts with a parenthetical hint like "(What is
this project?)", replace the hint with real content as you fill it in.
"""

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