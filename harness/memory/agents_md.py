"""Cross-session memory: the AGENTS.md pattern.

The harness reads AGENTS.md from the workspace at session start and
injects its contents as a second system message. The agent uses its
existing `write` tool to update the file as it learns.
"""

from harness.tools.filesystem import WORKSPACE
from harness.tools.registry import tool

# The valid category names for remember(). Anything else is rejected.
VALID_CATEGORIES = {
    "project_identity",
    "conventions",
    "decisions",
    "gotchas",
    "active_tasks",
}

# Map from category identifier to the section header string in AGENTS.md.
CATEGORY_HEADERS = {
    "project_identity": "## Project Identity",
    "conventions": "## Conventions",
    "decisions": "## Decisions",
    "gotchas": "## Gotchas",
    "active_tasks": "## Active Tasks",
}


# The single memory file for the workspace.
AGENTS_MD_PATH = WORKSPACE / "AGENTS.md"


# The template written when AGENTS.md doesn't yet exist. The section
# headings and inline hints serve as guidance for the model — both
# when reading (it knows what each section is for) and when writing
# (it knows what kind of content belongs where).
AGENTS_MD_TEMPLATE = """# Project Memory

> This file is the agent's durable memory across sessions. The harness
> loads it at the start of every session. Updates happen two ways:
> the `remember(category, entry)` tool for in-session writes, and an
> end-of-session consolidation step that reviews the whole conversation
> and updates this file before the sandbox tears down.

## Project Identity
(What is this project? What does it do? Who is it for?)

## Conventions
(Code style, naming patterns, libraries used, tool preferences.)

## Decisions
(Choices that have been made and the reasoning behind them.)

## Gotchas
(Things to remember about this codebase that might trip up future
sessions — quirks, non-obvious dependencies, common mistakes.)

## Active Tasks
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

def validate_agents_md_structure(text: str) -> bool:
    """Check that all five expected section headers are present.

    Returns True if the text is a valid AGENTS.md structure, False otherwise.
    Used by the end-of-session consolidation step to guard against a
    malformed rewrite from the model.
    """
    return all(header in text for header in CATEGORY_HEADERS.values())


def save_agents_md(text: str) -> None:
    """Atomically write the given text as the new AGENTS.md content.

    Uses write-to-temp-then-rename to guarantee either the whole write
    lands or nothing does. Callers should validate structure first.
    """
    agents_md_path = AGENTS_MD_PATH  # from existing module-level constant
    tmp_path = agents_md_path.with_suffix(".md.tmp")
    tmp_path.write_text(text)
    tmp_path.replace(agents_md_path)


@tool
def remember(category: str, entry: str) -> str:
    """Add an entry to a specific section of AGENTS.md.

    Use this to record something durable during the session — a project
    fact you learned, a convention observed, a decision made, a gotcha
    encountered, or a task now in progress. The entry appends to the
    named section; existing entries are preserved.

    Categories:
    - project_identity: what this codebase is, what it does, who uses it
    - conventions: code style, patterns, libraries in use
    - decisions: choices made and the reasoning behind them
    - gotchas: quirks, non-obvious dependencies, things that tripped up prior sessions
    - active_tasks: what's currently being worked on (clear when complete)

    Returns a short confirmation, or an error if the category is invalid.
    """
    # Step 1: validate the category. Reject anything not in the fixed set.
    if category not in VALID_CATEGORIES:
        valid = ", ".join(sorted(VALID_CATEGORIES))
        return f"[remember] Invalid category '{category}'. Valid: {valid}"

    # Step 2: load the current file (auto-creates if missing).
    current = load_agents_md()

    # Step 3: find the section header and append the entry after it.
    # We append rather than prepend so section reads chronologically.
    header = CATEGORY_HEADERS[category]
    if header not in current:
        # This shouldn't happen — load_agents_md always writes the template
        # with all headers present. If it did happen, the file is corrupted.
        return f"[remember] AGENTS.md is missing the '{header}' section. Aborting write."

    # Step 4: build the updated content — insert the new entry as a bullet
    # right below the section header. The exact insertion is: split at the
    # header, add the entry to the section, reassemble.
    lines = current.split("\n")
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if line == header and not inserted:
            new_lines.append(f"- {entry}")
            inserted = True

    updated = "\n".join(new_lines)

    # Step 5: atomic write.
    save_agents_md(updated)

    return f"[remember] Added to {category}: {entry}"

