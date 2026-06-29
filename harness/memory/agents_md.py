"""Cross-session memory: the AGENTS.md pattern.

The harness reads AGENTS.md from the workspace at session start and
injects its contents as a second system message. The agent uses its
existing `write` tool to update the file as it learns.
"""

from harness.tools.filesystem import WORKSPACE


# The single memory file for the workspace.
AGENTS_MD_PATH = WORKSPACE / "AGENTS.md"


# The template written when AGENTS.md doesn't yet exist. The section
# headings and inline hints serve as guidance for the model — both
# when reading (it knows what each section is for) and when writing
# (it knows what kind of content belongs where).
AGENTS_MD_TEMPLATE = """\
# Project Memory

> This file is the agent's durable memory across sessions. The harness
> loads it at the start of every session. Update it whenever you learn
> something worth remembering for future sessions.

## Project context
(What is this project? What does it do? Who is it for?)

## Conventions
(Code style, naming patterns, libraries used, tool preferences.)

## Decisions
(Choices that have been made and the reasoning behind them.)

## Gotchas
(Things to remember about this codebase that might trip up future
sessions — quirks, non-obvious dependencies, common mistakes.)

## Active tasks
(What's currently being worked on. Clear entries when work completes.)
"""


def load_agents_md() -> str:
    """Read AGENTS.md from the workspace, creating it from a template if missing.

    Returns the file's full contents as a string, ready to be used as
    the content of a system message.
    """
    # Step 1: ensure the file exists. If not, write the template — this is
    # the hard constraint that guarantees the agent never sees a missing-memory
    # state. Parallel to git's auto-init in 3.3.
    if not AGENTS_MD_PATH.exists():
        AGENTS_MD_PATH.write_text(AGENTS_MD_TEMPLATE)

    # Step 2: read and return the contents. The harness will wrap this in
    # a system message before sending it to the model.
    return AGENTS_MD_PATH.read_text()

