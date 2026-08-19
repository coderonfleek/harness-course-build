"""Bash tool: the meta-tool that gives the agent access to the shell.
"""

import subprocess
from harness.tools.filesystem import WORKSPACE
from harness.tools.registry import tool
from harness.config import BASH_TIMEOUT, ALLOW_LIST, DENY_LIST
from harness.sandbox import Sandbox

# Module-level sandbox reference. Set once at startup by agent.py via
# set_sandbox(). The bash tool reads this at call time. This is the
# smallest step above hardcoding — the caller (agent.py) constructs the
# dependency; the module that needs it accepts one at setup time.
_sandbox: Sandbox | None = None

def set_sandbox(sb: Sandbox) -> None:
    """Called by agent.py at startup to wire the sandbox into this module."""
    global _sandbox
    _sandbox = sb

# Characters that separate chained commands in bash. We check the first
# token of every segment, so `pip install foo && rm -rf /` gets both
# `pip` and `rm` checked, not just `pip`.
_CHAIN_SEPARATORS = ("&&", "||", ";", "|")

def _first_token(segment: str) -> str:
    """Return the first whitespace-separated token of a command segment."""
    segment = segment.strip()
    if not segment:
        return ""
    return segment.split()[0]


def _segments(command: str) -> list[str]:
    """Split a shell command on chain separators into its segments.

    This is a first-token-checker helper, not a full shell parser. It
    won't catch every edge case (backticks, $(...), complex quoting)
    but it handles the common chain patterns.
    """
    segments = [command]
    for sep in _CHAIN_SEPARATORS:
        segments = [
            piece
            for seg in segments
            for piece in seg.split(sep)
        ]
    return segments

def _check_policy(command: str) -> str | None:
    """Check the command against the allow-list and deny-list.

    Returns None if the command is permitted, or an error string if not.
    Deny-list wins on conflict — a command on both lists is rejected.
    """
    # Step 1: extract the first token from every chain segment.
    tokens = [_first_token(s) for s in _segments(command)]
    tokens = [t for t in tokens if t]  # drop empty segments

    # Step 2: deny-list check first (deny wins).
    if DENY_LIST:
        for token in tokens:
            if token in DENY_LIST:
                return (
                    f"[policy] '{token}' is on the deny-list — refusing to run."
                )

    # Step 3: allow-list check (empty = permissive, so skip when empty).
    if ALLOW_LIST:
        for token in tokens:
            if token not in ALLOW_LIST:
                return (
                    f"[policy] '{token}' is not on the allow-list — "
                    f"refusing to run. Allow-list currently permits: "
                    f"{', '.join(sorted(ALLOW_LIST))}."
                )

    return None



def _combine_output(stdout: str, stderr: str) -> str:
    """Combine stdout and stderr into a single string for the model.

    stdout comes first (usually the primary output). stderr is appended
    when non-empty with a marker so the model can tell them apart without
    us structuring the return as a dict.
    """
    # Step 1: normalize whitespace on both streams.
    stdout = stdout.strip()
    stderr = stderr.strip()

    # Step 2: return only what's non-empty; add a marker between them
    # so the model can distinguish output stream from error stream.
    if stdout and stderr:
        return f"{stdout}\n--- stderr ---\n{stderr}"
    return stdout or stderr


@tool
def bash(command: str) -> str:
    """Execute a bash command in the workspace and return its combined output.

    The command runs with the workspace as the working directory. Full shell
    interpretation is applied — pipes, redirects, variable expansion, and
    command chaining all work as they would at a real terminal.

    Use bash for anything the filesystem or git tools don't cover: running
    scripts, invoking system utilities (grep, find, curl, wc, sort),
    installing packages (pip install ...), or executing code the agent has
    written to a file (python script.py, node script.js).

    Returns stdout followed by stderr (when non-empty). Exit codes other
    than 0 are surfaced with a [bash exit N] prefix so the model can
    recognize failures and recover.

    Commands are subject to the allow-list and deny-list policy defined at the top of this module. If the policy refuses a command, an error
    is returned instead of executing it.
    """

    # Step 1: policy check before touching the sandbox. Fast rejection —
    # no container work, no side effects.
    policy_error = _check_policy(command)
    if policy_error:
        return policy_error

    # Step 2: fail loudly if the sandbox wasn't wired up. This shouldn't
    # happen in normal usage — agent.py calls set_sandbox at startup —
    # but if it does, the error message says what went wrong.
    if _sandbox is None:
        return (
            "[bash error] Sandbox not initialized. This is a harness bug; "
            "agent.py should have called set_sandbox() at startup."
        )

    # Step 3: ask the sandbox to execute. It returns (exit_code, stdout, stderr).
    exit_code, stdout, stderr = _sandbox.execute(command)

    # Step 4: combine output. Same _combine_output helper as before.
    output = _combine_output(stdout, stderr) or "(no output)"

    # Step 5: surface non-zero exit code with the [bash exit N] prefix.
    if exit_code != 0:
        return f"[bash exit {exit_code}] {output}"

    return output