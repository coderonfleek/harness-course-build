"""The recall tool: query the RAG index and return relevant chunks."""

import os
from pathlib import Path

import chromadb
from openai import OpenAI

from harness.tools.registry import tool
from harness.config import (
    RAG_INDEX_PATH,
    RAG_EMBEDDING_MODEL,
    RAG_DEFAULT_RESULTS,
)
from harness.rag.indexer import COLLECTION_NAME


def _project_root() -> Path:
    """Absolute path to the project root (two dirs up from this file)."""
    return Path(__file__).parent.parent.parent


def _format_results(chunks: list[str], sources: list[str], query: str) -> str:
    """Turn the retrieved chunks into markdown-ish text for the model.

    Each result is a numbered block with the source path and the chunk
    content. The header names the query so the model can double-check
    the retrieval matched what it asked for.
    """
    lines = [f"### Recall results for: {query}", ""]
    if not chunks:
        lines.append("(no matching chunks found)")
        return "\n".join(lines)

    for i, (chunk, source) in enumerate(zip(chunks, sources), start=1):
        lines.append(f"**Result {i}** — from `{source}`")
        lines.append(chunk.strip())
        lines.append("")

    return "\n".join(lines)


@tool
def recall(query: str, max_results: int = None) -> str:
    """Search the knowledge corpus for content relevant to a query.

    Use this to look up information from the project's knowledge base —
    internal docs, past decisions, incident writeups, runbooks. The tool
    performs semantic search: it finds chunks whose meaning is close to
    the query, not just literal keyword matches.

    Use recall when:
    - The user asks about internal/private content the model wouldn't
      know from training (specific projects, teams, past decisions,
      internal jargon)
    - You need context from a large corpus that wouldn't fit in AGENTS.md
    - The question is semantic — "what did we decide about X" rather
      than a specific file lookup

    Do NOT use recall for:
    - Session memory (that's AGENTS.md, already in your context)
    - Public knowledge the model reliably knows (general programming,
      well-known frameworks)
    - Current-events lookups (that's web_search)
    - Structured API references (that's better served by a purpose-built
      tool if one exists)

    Args:
        query: A specific, focused query. Natural phrasing works best.
        max_results: How many chunks to return. Defaults to the config
            default (typically 5). Lower for narrow lookups, higher when
            you want broader context.

    Returns:
        Formatted text with numbered results, each showing the source
        path and the retrieved chunk.
    """
    # Step 1: resolve max_results — fall back to config default if not specified.
    if max_results is None:
        max_results = RAG_DEFAULT_RESULTS

    # Step 2: check the API key. Fail fast with a clear message.
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return "[recall error] OPENAI_API_KEY is not set."

    # Step 3: open the ChromaDB collection. If the index doesn't exist,
    # tell the user how to build it — no point burying the fix in a
    # generic error.
    index_dir = _project_root() / RAG_INDEX_PATH
    if not index_dir.exists():
        return (
            "[recall error] No index found. Build the index first with: "
            "python -m harness.rag_index"
        )

    try:
        chroma_client = chromadb.PersistentClient(path=str(index_dir))
        collection = chroma_client.get_collection(name=COLLECTION_NAME)
    except Exception as e:
        return (
            f"[recall error] Could not open the RAG index: {e}. "
            f"Try rebuilding with: python -m harness.rag_index"
        )

    # Step 4: embed the query using the same model as the corpus.
    # Using different models here would produce garbage results.
    try:
        client = OpenAI()
        embed_response = client.embeddings.create(
            model=RAG_EMBEDDING_MODEL,
            input=[query],
        )
        query_embedding = embed_response.data[0].embedding
    except Exception as e:
        return f"[recall error] Embedding failed: {e}"

    # Step 5: query the collection for the top-K nearest neighbors.
    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=max_results,
        )
    except Exception as e:
        return f"[recall error] Query failed: {e}"

    # Step 6: extract the documents and their source metadata.
    # ChromaDB returns each field as a list-of-lists (one inner list
    # per query embedding); we sent one query, so we take index [0].
    documents = results["documents"][0] if results.get("documents") else []
    metadatas = results["metadatas"][0] if results.get("metadatas") else []
    sources = [m.get("source", "(unknown)") for m in metadatas]

    return _format_results(documents, sources, query)

