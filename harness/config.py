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