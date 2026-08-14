"""Bash tool: the meta-tool that gives the agent access to the shell.
"""

import subprocess
from harness.tools.filesystem import WORKSPACE
from harness.tools.registry import tool


# Per-command timeout. Longer than git's 10s because bash covers real
# work (installs, network calls, running scripts) that legitimately
# takes 30-60 seconds.
BASH_TIMEOUT = 60


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
    """
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