"""Compaction: summarize older turns, offload full history, restart lean."""

import json
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from harness.config import (
    MODEL,
    COMPACTION_THRESHOLD,
    COMPACTION_KEEP_RECENT,
)


# Path to the on-disk offload — appended to on every compaction event.
def _log_path() -> Path:
    """Absolute path to the context log; at project root under .harness/."""
    return Path(__file__).parent.parent.parent / ".harness" / "context_log.jsonl"


# Load the summarization prompt from the prompts folder once at import.
SUMMARIZATION_PROMPT = (
    Path(__file__).parent.parent / "prompts" / "compaction.txt"
).read_text()


# --- Private helpers used by Compactor.compact() below ---
#
# Defined at module scope (not as methods) because they're pure functions
# on the messages list — no state, easy to reason about in isolation.
# Placed here, above the class, so reading order matches call order:
# each helper is defined before the code that calls it.
#
# NOTE on message shapes: the messages list in agent.py is a MIXED
# sequence. User/system/tool entries are plain dicts, but assistant
# entries appended after a completion are pydantic ChatCompletionMessage
# objects (that's how the OpenAI SDK's tool_call structures survive
# round-tripping). The little accessors below hide that difference so
# the rest of the compactor can treat both uniformly.


def _msg_get(msg, field: str, default=None):
    """Read a field from a message, whether it's a dict or a pydantic object.

    Mirrors dict.get() semantics — returns the field's value if present,
    otherwise the supplied default. This is the primitive that handles the
    mixed-shape reality of the messages list (dicts for user/system/tool
    entries, pydantic ChatCompletionMessage objects for assistant entries
    that came back from the OpenAI SDK).
    """
    if isinstance(msg, dict):
        return msg.get(field, default)
    return getattr(msg, field, default)


# The three semantic accessors below are thin one-liners over _msg_get.
# We keep them because they enforce the "role and content are strings,
# never None" invariant that most call sites expect — otherwise every
# `_msg_role(msg) == "user"` comparison would have to defend against None.


def _msg_role(msg) -> str:
    """Return the role of a message ('' if missing)."""
    return _msg_get(msg, "role", "") or ""


def _msg_content(msg) -> str:
    """Return the content of a message ('' if missing or None)."""
    return _msg_get(msg, "content", "") or ""


def _msg_tool_calls(msg):
    """Return the tool_calls list of a message, or None if absent."""
    return _msg_get(msg, "tool_calls", None)


def _split_system_head(messages: list) -> tuple[list, list]:
    """Split the leading run of system messages from the rest."""
    # The initial system messages (base prompt, AGENTS.md, etc.) are
    # loaded at session start and always come first. Find where the
    # first non-system message is; everything before is the head.
    for i, msg in enumerate(messages):
        if _msg_role(msg) != "system":
            return messages[:i], messages[i:]
    # All messages are system messages (unusual — happens if compaction
    # is called before any user turn). Return everything as head.
    return messages, []


def _split_recent(
    conversation: list, keep_recent_exchanges: int
) -> tuple[list, list]:
    """Split conversation into (recent, older) preserving message groupings.

    We count *user turns* backward from the end. Everything from the
    Nth-most-recent user turn onward is "recent"; earlier messages are
    "older". This keeps assistant responses and tool results attached
    to the user turns they belong to.
    """
    # Step 1: find the indices of user messages, walking backward.
    user_indices = [i for i, msg in enumerate(conversation) if _msg_role(msg) == "user"]

    if len(user_indices) <= keep_recent_exchanges:
        # Fewer user turns than we want to keep — nothing to compact.
        return conversation, []

    # Step 2: the split point is the index of the Nth-from-last user turn.
    split_index = user_indices[-keep_recent_exchanges]
    return conversation[split_index:], conversation[:split_index]


def _format_messages_for_summary(messages: list) -> str:
    """Render messages as text for the summarization prompt.

    Each message becomes "ROLE: content", with tool results labeled.
    Tool call blocks get flattened into a readable form. The output is
    what the summarization model sees as its input.
    """
    lines = []
    for msg in messages:
        role = _msg_role(msg).upper()
        content = _msg_content(msg)
        tool_calls = _msg_tool_calls(msg)

        # Assistant messages can include tool_calls in addition to content.
        if role == "ASSISTANT" and tool_calls:
            for tc in tool_calls:
                # A tool_call may itself be a dict or a pydantic object,
                # and its .function attribute may be either shape too.
                # _msg_get handles both at each layer.
                fn = _msg_get(tc, "function", {})
                fn_name = _msg_get(fn, "name", "")
                fn_args = _msg_get(fn, "arguments", "")
                lines.append(f"ASSISTANT (tool_call): {fn_name}({fn_args})")
            if content:
                lines.append(f"ASSISTANT: {content}")
        elif role == "TOOL":
            # Tool responses can be long; truncate for the summary input.
            display = content[:2000]
            if len(content) > 2000:
                display += "\n...[truncated]"
            lines.append(f"TOOL: {display}")
        else:
            lines.append(f"{role}: {content}")

    return "\n\n".join(lines)


def _offload(messages: list, summary: str) -> None:
    """Append the pre-compaction messages + summary to the on-disk log."""
    log_path = _log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert pydantic message objects to dicts for JSON serialization.
    # Plain dicts pass through unchanged.
    def _to_dict(msg):
        if isinstance(msg, dict):
            return msg
        # Pydantic v2 objects — model_dump gives a clean dict.
        if hasattr(msg, "model_dump"):
            return msg.model_dump(exclude_none=True)
        # Fallback — string representation, last resort.
        return {"role": _msg_role(msg), "content": _msg_content(msg)}

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message_count": len(messages),
        "summary": summary,
        "messages": [_to_dict(m) for m in messages],
    }

    with log_path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# --- The Compactor class ---


class Compactor:
    """Owns the compaction lifecycle: threshold check, summarize, offload, rebuild.

    A single Compactor instance lives for the whole agent session. It's
    invoked by agent.py between user turns.
    """

    def __init__(self, client: OpenAI):
        # The OpenAI client — reused for the summarization call.
        self._client = client
        # Most recent prompt_tokens seen from the model's usage field.
        # Set by agent.py after each completion call.
        self._last_token_count: int = 0

    def record_token_usage(self, prompt_tokens: int) -> None:
        """Called by agent.py after every model completion.

        Stores the count so should_compact() can check it later.
        """
        self._last_token_count = prompt_tokens

    def get_last_token_count(self) -> int:
        """Return the last-seen prompt_tokens count.

        Used by agent.py to display the current context size to the user
        before each prompt, and internally to build the before-count in
        the compaction notice.
        """
        return self._last_token_count

    def should_compact(self) -> bool:
        """True when the last-seen token count exceeded the threshold."""
        return self._last_token_count >= COMPACTION_THRESHOLD

    def approximate_char_count(self, messages: list) -> int:
        """Approximate token count of messages via character length / 4.

        Used for the display-only 'after' number in the compaction notice —
        we don't have a real usage count until the next model call runs.
        Delegates to _msg_content so the shape-awareness (dict vs pydantic
        assistant messages) stays confined to this module.
        """
        return sum(len(_msg_content(m)) for m in messages) // 4

    def compact(self, messages: list) -> list | None:
        """Run one compaction pass.

        Returns:
            The rebuilt messages list if compaction ran, or None if it
            was a no-op (nothing older to summarize, or the summarization
            model returned an empty result). Callers should treat None as
            "no work was done" — do not overwrite `messages` with it.

        Sequence:
        1. Split messages into (preserved system prompts, older, recent).
        2. Ask the model to summarize the older portion.
        3. Offload the full messages list to disk.
        4. Rebuild: system prompts + summary system message + recent.
        """
        # Step 1: partition the messages. System messages at the top of
        # the list stay verbatim (they are the base prompt, AGENTS.md,
        # MCP-usage notes, etc.). Everything else divides into "older"
        # and "recent" using KEEP_RECENT.
        system_head, conversation = _split_system_head(messages)
        recent, older = _split_recent(conversation, COMPACTION_KEEP_RECENT)

        # Step 2: if there's nothing older to summarize (session was
        # short — fewer than KEEP_RECENT user turns exist yet), signal
        # no-op to the caller. Returning the messages unchanged would
        # look like success to a caller that doesn't inspect closely.
        if not older:
            return None

        # Step 3: build the summarization payload. We send the older
        # messages as a formatted block within a single user message,
        # rather than as a real conversation, so the summarization call
        # is self-contained.
        older_as_text = _format_messages_for_summary(older)
        summarization_messages = [
            {"role": "system", "content": SUMMARIZATION_PROMPT},
            {"role": "user", "content": older_as_text},
        ]

        # Step 4: call the model for the summary. No tools passed — this
        # is a text-only call, same pattern as consolidation in 6.2.
        response = self._client.chat.completions.create(
            model=MODEL,
            messages=summarization_messages,
        )
        summary_text = (response.choices[0].message.content or "").strip()

        if not summary_text:
            # Summarization returned empty — same no-op signal, and
            # log the surprise since this shouldn't normally happen.
            print("[compactor] Summarization returned empty text. Skipping compaction.")
            return None

        # Step 5: offload the full pre-compaction messages to disk.
        # Wrapped in try/except — a disk-write failure shouldn't block
        # compaction; the summary still lets the session continue.
        try:
            _offload(messages, summary_text)
        except Exception as e:
            print(f"[compactor] Offload failed: {type(e).__name__}: {e}. Continuing anyway.")

        # Step 6: rebuild the messages. System head preserved, followed
        # by the summary as a new system message, followed by the recent
        # turns verbatim.
        summary_message = {
            "role": "system",
            "content": f"## Compacted Summary\n\n{summary_text}",
        }
        rebuilt = system_head + [summary_message] + recent

        # Step 7: reset the last-seen token count. We no longer have an
        # accurate reading — the messages list changed shape after the
        # last model call. Setting to 0 causes agent.py's pre-prompt
        # display to suppress until the next real completion updates it
        # with an accurate number. Better than showing a stale count
        # from before compaction, which confuses users into thinking
        # compaction didn't shrink anything.
        self._last_token_count = 0

        return rebuilt