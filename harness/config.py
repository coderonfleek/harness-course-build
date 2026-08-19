"""Runtime configuration for the harness.

All values here are *configuration* — knobs someone using the harness
would adjust without changing behavior code. Deep behavioral constants
(e.g., BUDGET_HIT_MESSAGE, AGENTS_MD_TEMPLATE) stay in their behavior
modules, because they're part of what the harness *is*, not values it
happens to use.

"""

# -- Model configuration --
# The chat completion model the harness calls. Any OpenAI-compatible
# model ID works (e.g., "gpt-4o-mini", "kimi-k2.6").
MODEL: str = "gpt-4o-mini"


# -- ReAct loop bounds --
# Maximum number of tool-call rounds per user turn. When hit, the harness
# forces the model to summarize what it did and hand control back to the
# user. See Lesson 4.4 for the loop's design and behavior.
STEP_BUDGET: int = 25


# -- Tool timeouts (seconds) --
# Per-command execution timeouts. Longer for bash (covers real work like
# installs) than for git (should always be fast on a local repo).
BASH_TIMEOUT: int = 60
GIT_TIMEOUT: int = 10


# -- Bash command policy --
# Allow-list of permitted commands (matched against the first token of
# each chain segment). Empty = permissive.
ALLOW_LIST: set[str] = set()

# Deny-list of forbidden commands. Empty = no denials.
# Precedence: deny wins on conflict. See Lesson 4.5.
DENY_LIST: set[str] = set()