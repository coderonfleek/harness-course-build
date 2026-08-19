"""Sandbox: a Docker container wrapper managing its lifecycle for the harness.

Owns the container. Exposes an execute() method for running commands inside.
The bash tool asks the sandbox to run things; the sandbox knows Docker; the
bash tool stays Docker-unaware.
"""

import uuid
import docker
from docker.errors import DockerException, NotFound

from harness.tools.filesystem import WORKSPACE
from harness.config import (
    SANDBOX_IMAGE,
    SANDBOX_WORKSPACE_PATH,
    SANDBOX_STARTUP_TIMEOUT,
    SANDBOX_CONTAINER_PREFIX,
    BASH_TIMEOUT,
)


class Sandbox:
    """Owns a Docker container for the agent's session.

    Lifecycle:
    - start() — clean up orphans from prior sessions, create and start a
      fresh container, bind-mount the workspace.
    - execute(command) — run a shell command inside the container, return
      the combined output plus exit code.
    - stop() — stop and remove the container.

    Use as a context manager where possible so teardown is guaranteed:

        with Sandbox() as sb:
            sb.execute("ls")
            ...

    Or call start()/stop() explicitly for cases where a context manager
    isn't the right shape (e.g., the agent's main loop in agent.py).
    """

    def __init__(self) -> None:
        # Step 1: connect to the Docker daemon. This raises DockerException
        # if the daemon isn't reachable (Docker Desktop not running, etc.).
        self._client = docker.from_env()

        # Step 2: give this container a unique name using our prefix.
        # The prefix lets us identify and clean up orphans; the UUID
        # suffix keeps each new container's name unique.
        self._container_name = f"{SANDBOX_CONTAINER_PREFIX}{uuid.uuid4().hex[:8]}"

        # Step 3: the container handle is set by start(); None until then.
        self._container = None

    def start(self) -> None:
        """Clean up any orphan containers, then create and start a fresh one."""
        # Step 1: sweep away any containers left behind by prior sessions.
        # Filter by name prefix — anything starting with agent-harness-
        # is ours (or was ours) and should be removed.
        self._cleanup_orphans()

        # Step 2: create and start the container with the workspace bind-mounted.
        # detach=True runs it in the background; without this the call would
        # block. tty=True keeps the container alive after startup (otherwise
        # a container with no active process exits immediately).
        self._container = self._client.containers.run(
            image=SANDBOX_IMAGE,
            name=self._container_name,
            command="sleep infinity",  # keeps the container running
            detach=True,
            tty=True,
            working_dir=SANDBOX_WORKSPACE_PATH,
            volumes={
                str(WORKSPACE): {
                    "bind": SANDBOX_WORKSPACE_PATH,
                    "mode": "rw",
                },
            },
        )

        # Step 3: wait for the container to reach the "running" state. Docker
        # is usually fast (sub-second), but on first pull of an image or on
        # a slow daemon it can take longer.
        self._wait_for_running()

    def execute(self, command: str) -> tuple[int, str, str]:
        """Execute a shell command inside the running container.

        Returns (exit_code, stdout, stderr). Times out if the command runs
        longer than BASH_TIMEOUT seconds.
        """
        if self._container is None:
            raise RuntimeError("Sandbox.execute() called before start()")

        # Wrap the command in `timeout N bash -c "..."` so the container's
        # own `timeout` utility handles it — that way the container itself
        # kills a hung process, not Python.
        wrapped = f'timeout {BASH_TIMEOUT} bash -c {_shell_quote(command)}'

        # exec_run returns an ExecResult with exit_code and output.
        # We ask for separate stdout/stderr streams via demux=True so the
        # bash tool can combine them however it wants.
        result = self._container.exec_run(
            wrapped,
            demux=True,
            workdir=SANDBOX_WORKSPACE_PATH,
        )

        exit_code = result.exit_code
        stdout_bytes, stderr_bytes = result.output
        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")

        return exit_code, stdout, stderr

    def stop(self) -> None:
        """Stop and remove the container."""
        if self._container is None:
            return  # never started, nothing to clean up

        try:
            self._container.stop(timeout=5)
        except DockerException:
            # If stop fails, force removal below will still get it.
            pass

        try:
            self._container.remove(force=True)
        except NotFound:
            # Already gone somehow. Fine.
            pass

        self._container = None

    def _cleanup_orphans(self) -> None:
        """Remove any leftover containers from prior crashed sessions."""
        # Find all containers whose name starts with our prefix. `all=True`
        # includes stopped containers (a container the OS killed but never
        # cleaned up will be in "exited" state, not running).
        orphans = self._client.containers.list(
            all=True,
            filters={"name": SANDBOX_CONTAINER_PREFIX},
        )

        for orphan in orphans:
            try:
                orphan.remove(force=True)
            except DockerException:
                # A concurrent process might be cleaning it up; ignore.
                pass

    def _wait_for_running(self) -> None:
        """Block until the container's state is 'running', up to the timeout."""
        import time
        deadline = time.time() + SANDBOX_STARTUP_TIMEOUT

        while time.time() < deadline:
            self._container.reload()  # refresh state from Docker
            if self._container.status == "running":
                return
            time.sleep(0.1)

        raise RuntimeError(
            f"Container {self._container_name} did not reach 'running' state "
            f"within {SANDBOX_STARTUP_TIMEOUT}s (current: {self._container.status})"
        )

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def _shell_quote(s: str) -> str:
    """Quote a string for safe interpolation into a shell command.

    We use single-quotes and escape any single-quote in the string itself
    by ending the quote, adding an escaped ', and reopening the quote.
    """
    return "'" + s.replace("'", "'\\''") + "'"