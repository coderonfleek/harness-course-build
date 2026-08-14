"""Bash tool: the meta-tool that gives the agent access to the shell.
"""

import subprocess
from harness.tools.filesystem import WORKSPACE
from harness.tools.registry import tool


# Per-command timeout. Longer than git's 10s because bash covers real
# work (installs, network calls, running scripts) that legitimately
# takes 30-60 seconds.
BASH_TIMEOUT = 60

# Allow-list of permitted commands (matched against the first token of
# each chain segment). Empty = permissive: any command not on the
# deny-list is allowed. Populate this to lock the agent down to a known
# set of tools.
ALLOW_LIST: set[str] = {"pip", "python", "python3", "ls", "cat"}

# Deny-list of forbidden commands (matched against the first token of
# each chain segment). Empty = no denials. Populate this to block
# specific dangerous commands even in an otherwise-permissive setup.
#
# Precedence: DENY_LIST wins on conflict. If a command is on both lists,
# it is rejected. This "fail closed" behavior matches production security
# policy standards (AWS IAM, firewalls, SELinux, etc.).
DENY_LIST: set[str] = {"pip"}

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

    # Policy check before touching subprocess. Fast rejection — 
    # no shell invoked, no side effects, just the error string returned
    # to the model so it can construct a different command.
    policy_error = _check_policy(command)
    if policy_error:
        return policy_error

    try:
        # Step 1: run the command with the workspace as the working directory.
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT,
            check=False,  # non-zero exits are surfaced as text, not raised
        )
    except subprocess.TimeoutExpired:
        # Step 2a: on timeout, tell the model explicitly what happened and
        # what its next move should be.
        return (
            f"[bash timeout after {BASH_TIMEOUT}s] Command was killed. "
            f"If this was expected to take longer, consider running it "
            f"in pieces or breaking the work up."
        )

    # Step 2b: assemble the output the model will see.
    output = _combine_output(result.stdout, result.stderr) or "(no output)"

    # Step 3: prepend a failure marker on non-zero exit, so the model
    # can see success/failure at a glance without parsing exit codes.
    if result.returncode != 0:
        return f"[bash exit {result.returncode}] {output}"

    return output