"""Chunk documents from the knowledge corpus, embed them via OpenAI,
and store vectors in a local ChromaDB collection.

Run via: python -m harness.rag_index
"""

import os
from pathlib import Path

import chromadb
from openai import OpenAI

from harness.config import (
    RAG_CORPUS_PATH,
    RAG_INDEX_PATH,
    RAG_EMBEDDING_MODEL,
    RAG_CHUNK_SIZE,
    RAG_CHUNK_OVERLAP,
)

# ChromaDB collection name — a single named collection holds all
# chunks from the corpus. Multiple collections would let us have
# multiple corpora indexed in parallel; for one-corpus teaching we
# keep it single.
COLLECTION_NAME = "knowledge"

# Rough tokens-to-characters ratio for OpenAI models: ~4 chars per
# token. Used to convert RAG_CHUNK_SIZE (tokens) to character counts
# for the splitter without a full tokenizer dependency.
CHARS_PER_TOKEN = 4


def _project_root() -> Path:
    """Absolute path to the project root (two dirs up from this file)."""
    return Path(__file__).parent.parent.parent


def _find_corpus_files(corpus_dir: Path) -> list[Path]:
    """Return all .md and .txt files under the corpus directory, recursively."""
    files = list(corpus_dir.rglob("*.md")) + list(corpus_dir.rglob("*.txt"))
    return sorted(files)


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into overlapping chunks of approximately chunk_size tokens.

    Prefers paragraph breaks (double newline), then sentence breaks, then
    hard character cuts as a last resort. Overlap smooths the case where
    relevant content straddles a chunk boundary.
    """
    # Step 1: convert token counts to character counts (rough approximation).
    chunk_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = overlap * CHARS_PER_TOKEN

    # Step 2: split into paragraphs first. Most markdown docs have
    # meaningful paragraph structure worth preserving.
    paragraphs = text.split("\n\n")

    # Step 3: pack paragraphs into chunks up to the size budget. If a
    # single paragraph is bigger than the budget, split it further.
    chunks: list[str] = []
    current_chunk = ""

    for paragraph in paragraphs:
        # Would adding this paragraph exceed the budget? If so, flush the
        # current chunk and start a new one (with overlap from the end
        # of the previous chunk).
        if len(current_chunk) + len(paragraph) > chunk_chars and current_chunk:
            chunks.append(current_chunk.strip())
            # Start next chunk with the tail of the previous one for overlap.
            current_chunk = current_chunk[-overlap_chars:] + "\n\n" + paragraph
        else:
            if current_chunk:
                current_chunk += "\n\n" + paragraph
            else:
                current_chunk = paragraph

    # Step 4: flush any remaining content.
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # Step 5: if any single chunk is still over budget (a very long
    # paragraph), hard-cut it. Rare in practice for typical markdown docs.
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= chunk_chars:
            final_chunks.append(chunk)
        else:
            # Hard character-based split for oversized chunks.
            for i in range(0, len(chunk), chunk_chars - overlap_chars):
                final_chunks.append(chunk[i:i + chunk_chars])

    return final_chunks


def _embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via the OpenAI embeddings API.

    Batched in a single API call to reduce round-trips. The embeddings
    API accepts up to 2048 inputs per call; we assume corpora fit in
    one batch (adjust for larger corpora).
    """
    response = client.embeddings.create(
        model=RAG_EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def build_index() -> None:
    """Read the corpus, chunk it, embed each chunk, store in ChromaDB.

    Called by `python -m harness.rag_index`. Wipes any existing index
    for the collection and rebuilds from scratch — small corpora rebuild
    in seconds, and full rebuild avoids stale-content edge cases.
    """
    project_root = _project_root()
    corpus_dir = project_root / RAG_CORPUS_PATH
    index_dir = project_root / RAG_INDEX_PATH

    # Step 1: validate the corpus exists.
    if not corpus_dir.is_dir():
        raise FileNotFoundError(
            f"Corpus directory not found at {corpus_dir}. "
            f"Create it or set RAG_CORPUS_PATH in config.py."
        )

    # Step 2: validate the API key up front — no point starting a long
    # indexing job just to fail on the first embed call.
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set in the environment.")

    # Step 3: find the corpus files.
    files = _find_corpus_files(corpus_dir)
    if not files:
        raise FileNotFoundError(
            f"No .md or .txt files found under {corpus_dir}."
        )
    print(f"Found {len(files)} files under {corpus_dir}")

    # Step 4: chunk each file. Track chunks alongside their source path
    # so retrieval results can cite where each chunk came from.
    all_chunks: list[str] = []
    all_metadata: list[dict] = []
    for file_path in files:
        text = file_path.read_text()
        chunks = _chunk_text(text, RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP)
        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_metadata.append({
                "source": str(file_path.relative_to(project_root)),
                "chunk_index": i,
            })
    print(f"Chunked into {len(all_chunks)} chunks")

    # Step 5: embed all chunks in one API call.
    print(f"Embedding via {RAG_EMBEDDING_MODEL}...")
    client = OpenAI()
    embeddings = _embed_texts(client, all_chunks)

    # Step 6: reset the ChromaDB collection and write chunks + embeddings.
    # Reset (rather than upsert) guarantees a clean rebuild.
    chroma_client = chromadb.PersistentClient(path=str(index_dir))
    try:
        chroma_client.delete_collection(COLLECTION_NAME)
    except Exception:
        # Collection didn't exist yet — that's fine.
        pass
    collection = chroma_client.create_collection(name=COLLECTION_NAME)

    # ChromaDB needs each entry to have a unique ID. Path + chunk index
    # gives us a stable, meaningful identifier.
    ids = [f"{m['source']}#{m['chunk_index']}" for m in all_metadata]

    collection.add(
        ids=ids,
        documents=all_chunks,
        embeddings=embeddings,
        metadatas=all_metadata,
    )

    print(f"Index built: {len(all_chunks)} chunks stored at {index_dir}")