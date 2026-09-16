"""Runtime configuration for the harness.

All values here are *configuration* — knobs someone using the harness
would adjust without changing behavior code. Deep behavioral constants
(e.g., BUDGET_HIT_MESSAGE, AGENTS_MD_TEMPLATE) stay in their behavior
modules, because they're part of what the harness *is*, not values it
happens to use.

"""

from pathlib import Path

# -- Workspace --   
# The workspace directory. All filesystem tools operate inside this;
# the sandbox bind-mounts it; the offloader writes tool outputs into
# a subdirectory (.tool_outputs/) under it.
#
# Defined here (not in filesystem.py) so other modules can import it
# without pulling in the tool registry — avoids a circular import when
# harness/context/offloader.py needs the workspace path.
WORKSPACE: Path = Path("./workspace").resolve()
WORKSPACE.mkdir(exist_ok=True)


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
ALLOW_LIST: set[str] = {
    # Language interpreters and package managers
    "python", "python3", "pip", "pip3",
    # Basic file inspection
    "ls", "cat", "wc", "grep", "find", "head", "tail",
    # Network tools (the agent may want to probe the URL first)
    "curl",
    # System info
    "which", "echo", "pwd",
    # Git — though the agent should prefer the git_* tools first
    "git",
}

# Deny-list of forbidden commands. Empty = no denials.
# Precedence: deny wins on conflict. See Lesson 4.5.
DENY_LIST: set[str] = {
    # Destructive commands with no place in a scraping task
    "rm", "sudo", "dd", "mkfs", "mv",
}

# -- Sandbox configuration --                                             
# The Docker image used for the containerized bash tool.          
# Custom image built from harness/sandbox/Dockerfile — Debian base with
# Python, Node 20, git, curl, jq, ripgrep, and other common CLIs.
# Rebuild with: docker build -f harness/sandbox/Dockerfile -t agent-harness:0.1 . 
SANDBOX_IMAGE: str = "agent-harness:0.1"

# Where the workspace is mounted inside the container. All bash commands
# see this path as their working directory. Bind-mounted from the host's
# workspace/ directory, so files written here appear on the host too.
SANDBOX_WORKSPACE_PATH: str = "/workspace"

# How long to wait for the container to reach "running" state after
# creation. If Docker takes longer than this, something is wrong
# (image not pulled yet, daemon slow, etc.) and we fail loudly.
SANDBOX_STARTUP_TIMEOUT: int = 30

# Prefix for container names. Used to identify our containers when
# cleaning up orphans from previous sessions on startup.
SANDBOX_CONTAINER_PREFIX: str = "agent-harness-"

# -- Web search -- 
# Default number of search results to return per query. The model can
# override this via the max_results argument on the web_search tool.
# Five is a middle ground — enough context to be useful, not so many
# that the response bloats.
WEB_SEARCH_MAX_RESULTS: int = 5

# -- RAG memory --    
# Path to the knowledge corpus (documents to be indexed and retrieved
# from). Lives at project root, not in workspace, so it survives
# workspace clean-slates.
RAG_CORPUS_PATH: str = "knowledge"

# Path to ChromaDB's persistent storage. Also at project root; gitignored
# because it's derived from the corpus and regenerable in seconds.
RAG_INDEX_PATH: str = ".rag_index"

# OpenAI's embedding model. text-embedding-3-small is cheap
# (~$0.02 per 1M tokens) and good enough for most retrieval tasks.
RAG_EMBEDDING_MODEL: str = "text-embedding-3-small"

# Target chunk size in tokens (approximate). Balances retrieval precision
# (smaller = more precise hits) against context per hit (larger = more
# useful passages).
RAG_CHUNK_SIZE: int = 500

# Overlap between adjacent chunks, in tokens. Smooths the case where
# relevant content straddles a chunk boundary.
RAG_CHUNK_OVERLAP: int = 50

# Default number of results the recall tool returns. The model can
# override this via max_results.
RAG_DEFAULT_RESULTS: int = 5

# -- Context management --        
# Token threshold at which the harness compacts the message history.
# Chosen to leave headroom below common context limits (128K) for the
# summarization call itself plus the next turn.
#
# Production systems typically calculate this as a percentage of the
# model's context window (e.g., 60% of ctx_window) so the same code
# adapts across models with different limits. We use a fixed value here
# for teaching — one number in config, easy to reason about and adjust.
COMPACTION_THRESHOLD: int = 60_000

# Number of recent user+assistant exchanges (including their tool
# results) to preserve verbatim after compaction. Everything older
# gets summarized. Small enough that summarization is meaningful,
# large enough that in-flight work isn't lost.
COMPACTION_KEEP_RECENT: int = 3


# -- Tool call offloading -- 
# Threshold above which a tool output gets offloaded to disk instead of
# landing fully in context. Approximate — measured in characters at a
# rough 4-chars-per-token conversion. Below this size, tool outputs pass
# through unchanged; above it, only a head and tail remain in context
# and the full output lands in workspace/.tool_outputs/.
#
# Fixed value across all tools for teaching. Production systems usually
# tune per-tool: web_search returns are designed to be ~1-2k tokens
# and a low threshold offloads all of them; bash outputs are more
# variable and a lower threshold makes sense. Same code shape — just
# a lookup table instead of one constant.
OFFLOAD_THRESHOLD_TOKENS: int = 1_000

# When an output IS offloaded, keep this many tokens at the start and
# at the end. Head + tail captures the case where value lives at
# either end (setup lines vs. summary lines). The middle is where
# bulk log output usually sits — that's what we send to disk.
OFFLOAD_HEAD_TOKENS: int = 300
OFFLOAD_TAIL_TOKENS: int = 300

# -- Planning support -- 
# The name of the plan file inside the workspace. Kept as a constant
# (not user-configurable per session) so the model can be told about
# the exact filename in the system prompt.
PLAN_FILENAME: str = "plan.md"

# How often (in assistant turns) the harness injects a reminder to
# update the plan. Only fires when plan.md exists and has at least
# one open item. Same shape as compaction firing on a threshold —
# harness owns when, model owns what.
#
# Lower = more nagging but tighter plan freshness. Higher = quieter
# but more plan drift. 2 allows it to trigger constantly for updates: plans stay fresh within
# a small window of turns, without spamming every turn.
PLAN_REMINDER_INTERVAL: int = 2

# Maximum characters to inject from plan.md at session start. Plans
# rarely get near this — a normal plan is a few hundred chars. This
# just caps a pathological case (someone accidentally pipes a huge
# file into plan.md, session start would otherwise inject the whole
# thing). At 4 chars/token, ~5000 chars ≈ 1250 tokens.
PLAN_INJECTION_MAX_CHARS: int = 5000

# -- Ralph loop --  
# Maximum number of times Ralph will force continuation within a single
# user turn. When hit, Ralph prints a warning and returns control to
# the user instead of forcing again.
#
# Same shape as STEP_BUDGET for the ReAct loop — a hard cap that
# prevents runaway loops when the agent genuinely can't complete
# some task. Tune down if forced continuations are wasting spend;
# tune up if you're doing genuinely long multi-step work and hitting
# the cap prematurely.
RALPH_MAX_CONTINUATIONS: int = 10

# The synthetic user message injected when Ralph forces continuation.
# Chosen to be minimal — the agent already sees the plan via
# session-start injection, so "continue" is enough to kick off the
# next ReAct loop iteration.
RALPH_CONTINUATION_PROMPT: str = "continue"

# Model used for Ralph's semantic evaluation. Same as MODEL by default;
# separated in case you want to use a cheaper/faster model for
# evaluation (the semantic check is a short, structured call — a
# smaller model often works fine here).
RALPH_EVAL_MODEL: str = MODEL

# Prompt for the semantic evaluator. Structured output (DONE / NOT_DONE
# prefix) makes Ralph's parsing trivial.
RALPH_EVAL_PROMPT: str = """You are evaluating whether an agent has completed a user's task.

Original user goal:
{goal}

Current plan state (from plan.md):
{plan}

Recent conversation summary:
{recent_summary}

Has the goal been met? Answer with exactly one of:
- "DONE: <one-sentence reason>" if the goal is fully satisfied
- "NOT_DONE: <one-sentence reason>" if meaningful work remains

Be strict — if the agent's work is partial, incomplete, unverified, or if
there are open plan items that haven't been addressed, answer NOT_DONE."""

# -- Subagent spawning --    
# Step budget for subagents. Deliberately smaller than the parent's
# STEP_BUDGET (25) — subagents doing one focused task shouldn't need
# many rounds. If a subagent hits this cap, its task was likely too
# large and should have been broken into smaller subtasks by the
# parent's decomposition.
SUBAGENT_STEP_BUDGET: int = 15

# Default set of tool names exposed to subagents. Excludes:
#   - spawn_subagent (no recursive spawning)
#   - update_plan (parent owns the plan)
#   - remember (parent owns memory)
#
# Individual spawn_subagent() calls can override this via the `tools`
# parameter for tasks that need a narrower set.
SUBAGENT_DEFAULT_TOOLS: list[str] = [
    "read", "write", "list", "mkdir", "delete",
    "git_status", "git_diff", "git_add", "git_commit", "git_log", "git_checkout",
    "bash",
    "web_search",
    "recall",
]

# Directory where per-subagent execution logs are written. Each
# subagent gets its own JSONL file containing the full messages
# list — for post-hoc inspection when a subagent's summary needs
# to be debugged.
SUBAGENT_LOG_DIR: Path = Path(".harness/subagents").resolve()