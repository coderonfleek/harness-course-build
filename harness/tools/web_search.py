"""Web search tool: knowledge injection via Tavily's LLM-optimized search API.

Fills the temporal knowledge gap named in Lesson 6.1 — things the model
doesn't know because they happened after its training cutoff, or moving-
target facts that need verification.
"""

import os
from tavily import TavilyClient

from harness.tools.registry import tool
from harness.config import WEB_SEARCH_MAX_RESULTS


def _format_results(response: dict, query: str) -> str:
    """Turn Tavily's JSON response into markdown-ish text for the model.

    Structure: the LLM-generated answer summary at the top (usually the
    direct answer), then a numbered list of ranked sources with title,
    URL, and snippet.
    """
    # Step 1: extract the answer summary. Tavily's `answer` field is an
    # LLM synthesis of the top results — often a direct answer to the query.
    answer = response.get("answer") or "(no answer synthesized)"

    # Step 2: extract the ranked source list. Each result has title, url,
    # content (the snippet), and a relevance score.
    results = response.get("results") or []

    # Step 3: build the formatted output. Header first, then answer, then
    # each source as a numbered block.
    lines = [f"### Web search results for: {query}", "", "**Answer:**", answer, ""]

    if results:
        lines.append("**Sources:**")
        for i, result in enumerate(results, start=1):
            title = result.get("title", "(untitled)")
            url = result.get("url", "")
            content = result.get("content", "").strip()
            lines.append(f"{i}. **{title}** — {url}")
            if content:
                lines.append(f"{content}")
            lines.append("")
    else:
        lines.append("(no sources returned)")

    return "\n".join(lines)


@tool
def web_search(query: str, max_results: int = None) -> str:
    """Search the web for current information and return ranked results.

    Use this when the answer requires information the model wouldn't know
    from training — post-cutoff events, current software versions, recent
    releases, moving-target facts (prices, availability, official
    announcements). Also useful for verifying claims where accuracy
    matters.

    Do NOT use for information the model reliably knows (well-established
    facts, general programming knowledge), for things in the workspace
    (read the files instead), or for things in AGENTS.md (already in
    context). Search consumes API quota and adds latency; use it when it
    earns its place.

    Args:
        query: A specific, focused search query. Prefer natural phrasing
            over keyword-stuffing ("what version is the current node LTS"
            works better than "node lts version current").
        max_results: How many source results to return. Defaults to 5.
            Use fewer for narrow lookups, more for broader research.

    Returns:
        Formatted text with an answer summary followed by the ranked
        source list. Each source has title, URL, and a snippet.
    """
    # Step 1: resolve max_results — fall back to config default if not specified.
    if max_results is None:
        max_results = WEB_SEARCH_MAX_RESULTS

    # Step 2: check the API key. Failing fast with a clear message beats
    # a generic auth error from the SDK.
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return (
            "[web_search error] TAVILY_API_KEY is not set. Get a key at "
            "tavily.com (free tier: 1000 requests/month) and add it to "
            "your .env file as: TAVILY_API_KEY=tvly-your-key-here"
        )

    # Step 3: perform the search. include_answer=True asks Tavily to
    # synthesize an answer from the top results — usually the most useful
    # single piece of the response for the model.
    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            include_answer=True,
        )
    except Exception as e:
        # Any API error — network, rate limit, malformed response —
        # returns as text so the model can react and possibly retry.
        return f"[web_search error] Search failed: {e}"

    # Step 4: format and return.
    return _format_results(response, query)

