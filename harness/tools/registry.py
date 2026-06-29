"""Tool registry: stores tools, generates schemas, dispatches calls."""

from dataclasses import dataclass
from typing import Any, Callable
from pydantic import TypeAdapter
import inspect


@dataclass
class Tool:
    """A registered tool — wraps a Python function with its metadata."""
    name: str
    description: str
    function: Callable
    schema: dict


class ToolRegistry:
    """Holds tools, exposes their schemas, dispatches incoming calls."""

    def __init__(self) -> None:
        # Tools indexed by name for O(1) dispatch lookup.
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        # Last-write-wins if the same name is registered twice.
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict]:
        """Return the list of tool schemas in the OpenAI tools= format."""
        # Wrap each tool's schema in OpenAI's required {"type": "function"} envelope.
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in self._tools.values()
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """Call the named tool with the given arguments. Returns its string output."""
        # Reject unknown tool names — return an error the model can read and recover from.
        if name not in self._tools:
            return f"error: unknown tool '{name}'"

        # Run the tool, catching any exception so a buggy call doesn't crash the loop.
        # The model sees the error string and decides what to do next.
        try:
            result = self._tools[name].function(**arguments)
            return str(result)
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"


# The single global registry the rest of the harness imports.
registry = ToolRegistry()


def tool(func: Callable) -> Callable:
    """
    Decorator: register a function as a tool.

    Reads the function's name, docstring, and type-hinted parameters
    to build the OpenAI tool schema. Adds it to the global registry.
    """
    # Step 1: extract metadata from the function itself.
    name = func.__name__
    description = inspect.getdoc(func) or ""

    # Step 2: build a JSON schema for the function's parameters via pydantic.
    # TypeAdapter introspects the signature and produces an OpenAI-compatible schema.
    schema = TypeAdapter(func).json_schema()

    # Step 3: register the tool in the global registry.
    registry.register(Tool(
        name=name,
        description=description,
        function=func,
        schema=schema,
    ))

    # Step 4: return the function unchanged so it can still be called directly.
    return func