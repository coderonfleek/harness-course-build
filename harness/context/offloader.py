"""Tool call offloading: intercept oversized tool outputs, keep only a
head/tail in context, write the full output to disk inside the workspace.

Called from ToolRegistry.dispatch() after each tool executes but before
its result is returned to the agent. Below-threshold results pass
through unchanged; above-threshold results get truncated with a marker
pointing at the on-disk copy.

Offload files live at workspace/.tool_outputs/. That means the agent's
existing read() and list() tools reach them naturally — no allow-list,
no special resolution logic. The marker path is workspace-relative, so
it looks like every other file path the agent already knows about.
"""

from datetime import datetime, timezone
from pathlib import Path

from harness.config import (
    OFFLOAD_THRESHOLD_TOKENS,
    OFFLOAD_HEAD_TOKENS,
    OFFLOAD_TAIL_TOKENS,
    WORKSPACE
)

# Same char-to-token approximation as the RAG chunker and the Compactor.
# Not exact, but consistent across the codebase.
CHARS_PER_TOKEN = 4

# The subdirectory under WORKSPACE where offloaded outputs land. Dot-prefixed
# so a default `ls` (or list()) doesn't clutter with harness bookkeeping;
# still reachable when the agent explicitly asks for it.
OFFLOAD_SUBDIR = ".tool_outputs"

# Tools whose output is NEVER offloaded, regardless of size. The `read`
# tool goes here because:
#   1. Agent calls read() to fetch specific content it wants. Offloading
#      its output would send that content right back to disk, then the
#      agent would have to retrieve again — infinite loop of offloads.
#   2. When the agent explicitly requests file content, respect the ask.
# Extensible: add other tools here that fetch-on-demand and shouldn't
# have their outputs re-offloaded.
NEVER_OFFLOAD_TOOLS = {"read"}


def _offload_dir() -> Path:
    """Absolute path to workspace/.tool_outputs/."""
    return WORKSPACE / OFFLOAD_SUBDIR


def _tokens_of(text: str) -> int:
    """Approximate token count via characters. Same conversion as elsewhere."""
    return len(text) // CHARS_PER_TOKEN


def _make_offload_path(tool_name: str) -> Path:
    """Build a unique path for one offload event.

    Format: workspace/.tool_outputs/<tool>_<UTC-timestamp>.txt. Timestamp
    prevents collision when the same tool offloads multiple times in one
    session; tool name in the filename makes it easy to see at a glance
    which tools produced the noise.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return _offload_dir() / f"{tool_name}_{timestamp}.txt"


def _workspace_relative(path: Path) -> str:
    """Convert an absolute offload path to a workspace-relative string.

    This is what appears in the truncation marker and what the agent
    passes to read(). Workspace-relative paths are the convention the
    agent already uses for every other file — offload paths look the
    same, so the agent doesn't need to reason about a separate path space.
    """
    return str(path.relative_to(WORKSPACE))


def maybe_offload(tool_name: str, result: str) -> str:
    """Apply the offload policy to one tool result.

    If the result is under the threshold, return it unchanged. Otherwise,
    write the full result to disk, print a user-visible notice, and
    return a truncated version with a marker pointing at the on-disk file.

    The returned string is what lands in the agent's context.

    Exception: tools in NEVER_OFFLOAD_TOOLS pass through unchanged,
    regardless of size.
    """
    # Step 1: never-offload set check. The agent asked for read()'s
    # content explicitly; offloading it would defeat the request and
    # risk an infinite loop.
    if tool_name in NEVER_OFFLOAD_TOOLS:
        return result

    # Step 2: cheap threshold check. Below threshold → nothing to do.
    total_tokens = _tokens_of(result)
    if total_tokens <= OFFLOAD_THRESHOLD_TOKENS:
        return result

    # Step 3: build the head and tail slices. Convert token budgets to
    # character counts for slicing. Head + tail is well under the
    # threshold, so the returned string won't itself be offload-eligible.
    head_chars = OFFLOAD_HEAD_TOKENS * CHARS_PER_TOKEN
    tail_chars = OFFLOAD_TAIL_TOKENS * CHARS_PER_TOKEN
    head = result[:head_chars]
    tail = result[-tail_chars:]

    # Step 4: write the full result to disk. Wrapped in try/except — a
    # write failure shouldn't crash the tool call. If we can't offload,
    # we return the original oversized result and let the model deal
    # with it (worse than offloading, better than crashing).
    try:
        offload_path = _make_offload_path(tool_name)
        offload_path.parent.mkdir(parents=True, exist_ok=True)
        offload_path.write_text(result)
    except Exception as e:
        print(f"[offloader] Write failed: {type(e).__name__}: {e}. Passing result through.")
        return result

    # Step 5: user-visible notice. Same pattern as compaction —
    # significant harness operations print to the terminal.
    relative_path = _workspace_relative(offload_path)
    offloaded_tokens = total_tokens - _tokens_of(head) - _tokens_of(tail)
    print(
        f"[Offloaded {offloaded_tokens:,} tokens from {tool_name} output "
        f"→ {relative_path}]"
    )

    # Step 6: build the truncated string the agent will see. Marker in
    # the middle names the workspace-relative path and the exact action
    # (call read() on this path).
    marker = (
        f"\n...[TRUNCATED: {offloaded_tokens:,} tokens of output offloaded. "
        f"Full output at {relative_path}. "
        f"Call read() on this path to fetch the missing content.]...\n"
    )
    return head + marker + tail