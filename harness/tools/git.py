"""Git tools: wrap commit/diff/log/checkout/branch operations as harness tools.

All operations run with the workspace as the working directory — pinned by
code, not supplied by the model. The workspace is auto-initialized as a git
repo at import time so the agent never has to remember to `git init`.
"""

import subprocess
from harness.tools.filesystem import WORKSPACE
from harness.tools.registry import tool
from harness.config import GIT_TIMEOUT


def _run_git(*args: str) -> str:
    """
    Run a git command in the workspace and return a combined output string.

    The first argument's working directory is hard-coded to WORKSPACE — the
    model cannot influence it. Non-zero exit codes are not raised; the
    output is returned with an exit-code note so the model can read it
    and decide what to do.
    """
    # Step 1: invoke git with argument-list form (no shell, so no injection risk).
    # cwd=WORKSPACE is the operation-scoping hard constraint.
    result = subprocess.run(
        ["git", *args],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=False,  # Don't raise on non-zero exit; the model handles errors.
    )

    # Step 2: extract the command's output text. Prefer stdout; fall back to
    # stderr only when stdout is empty, because some git commands (like `log`
    # on an empty repo) put useful information on stderr instead.
    output = result.stdout.strip() or result.stderr.strip()

    # Step 3: if git returned non-zero, prepend an exit-code note so the
    # model can recognize and recover from errors without parsing exit codes.
    if result.returncode != 0:
        return f"[git exit {result.returncode}] {output}"

    return output or "(no output)"


# Auto-initialize the workspace as a git repo at import time.
# This is a hard constraint preventing "I forgot to init" failures.
if not (WORKSPACE / ".git").exists():
    _run_git("init")
    # Set a default branch name and a baseline identity so commits work
    # out of the box, even on systems without global git config.
    _run_git("config", "user.name", "agent")
    _run_git("config", "user.email", "agent@harness.local")
    _run_git("symbolic-ref", "HEAD", "refs/heads/main")


@tool
def git_status() -> str:
    """Show the current working-tree status — modified, staged, and untracked files."""
    return _run_git("status", "--short")


@tool
def git_diff(path: str = "") -> str:
    """Show unstaged changes in the workspace. Optionally restrict to a single path."""
    # Step 1: build the argument list. Empty path = diff everything.
    args = ["diff"]
    if path:
        args.append(path)

    # Step 2: run git diff.
    return _run_git(*args)


@tool
def git_log(limit: int = 10) -> str:
    """Show recent commit history. Defaults to the last 10 commits."""
    # `--oneline` keeps the output compact; the model can ask for more detail
    # by reading specific commits later if needed.
    return _run_git("log", f"--max-count={limit}", "--oneline")


@tool
def git_commit(message: str) -> str:
    """Stage all current changes and commit them with the given message.
    Returns the commit hash on success, or an error on failure (e.g., nothing to commit)."""
    # Step 1: stage all current changes — equivalent to `git add -A`.
    stage_result = _run_git("add", "-A")

    # Step 2: commit. Non-zero exit here is common (nothing to commit);
    # _run_git returns the explanation to the model rather than raising.
    return _run_git("commit", "-m", message)


@tool
def git_checkout(ref: str) -> str:
    """Switch to a commit or branch. Used for both rollback (to an earlier commit)
    and branch switching (to a sibling branch)."""
    # The agent typically calls git_log first to find a commit hash to roll back to,
    # or git_branch to find a branch name to switch to.
    return _run_git("checkout", ref)


@tool
def git_branch(name: str = "") -> str:
    """List branches if called with no argument. Create a new branch if a name is given.
    Does NOT switch to the new branch — call git_checkout afterward to switch."""
    # Step 1: no name = list branches (with a star next to the current one).
    if not name:
        return _run_git("branch")

    # Step 2: name given = create a new branch at the current HEAD.
    # The agent must call git_checkout(name) separately to actually switch.
    return _run_git("branch", name)


