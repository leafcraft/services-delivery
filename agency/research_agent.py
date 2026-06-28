"""research_agent — self-reliant research analyst.

The research analyst is supposed to ground its findings in real prior
art — past projects, industry reports, methodology. A prompt that just
SAYS "pull from the knowledge base" has nothing to reason over on day 0;
the LLM either fabricates or returns empty. So this role pulls its OWN
corpus before it runs, exactly like a human analyst opening the KB.

@pre_compose  load_research_corpus — from the dev store (PROD: your KB /
              RAG semantic search) pull the prior-art CORPUS biased to
              the brief's practice area, plus the set of resolvable
              source_ids. The LLM reasons over THAT material and cites
              from it; citation-or-nothing is enforceable because the
              resolvable set is real.

All research JUDGMENT (which findings matter, the knowledge gaps, the
confidence) stays in the YAML prompt. This module only assembles the
inputs the analyst reasons over — no decision, no floor.
"""
from __future__ import annotations

from typing import Any

from leafmesh import LeafMeshLogger, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)


def _pick(data: Any, key: str, default: Any = "") -> Any:
    """Read a key from the input or its upstream_yields envelope."""
    if not isinstance(data, dict):
        return default
    if data.get(key) not in (None, ""):
        return data[key]
    uy = data.get("upstream_yields")
    if isinstance(uy, dict):
        if uy.get(key) not in (None, ""):
            return uy[key]
        for v in uy.values():
            if isinstance(v, dict) and v.get(key) not in (None, ""):
                return v[key]
    return default


def load_research_corpus(data: Any, ctx: Any) -> dict[str, Any]:
    """Pull the prior-art corpus the analyst reasons + cites over."""
    input_data = data if isinstance(data, dict) else {}
    eid = _pick(input_data, "engagement_id", "") or store.default_engagement_id()
    brief = store.get_brief(eid)
    practice_area = (
        brief.get("practice_area")
        or _pick(input_data, "practice_area", "")
    )
    corpus = store.research_corpus(practice_area)
    resolvable = sorted(store.corpus_source_ids())
    logger.info(
        f"[research] pre_compose eng={eid or '-'} "
        f"practice_area={practice_area or '-'} corpus={len(corpus)} "
        f"resolvable={len(resolvable)}"
    )
    return {
        "engagement_id": eid,
        "brief": brief,
        "practice_area": str(practice_area or "").lower(),
        "research_corpus": corpus,
        "resolvable_source_ids": resolvable,
    }


@pre_compose(context_processor=load_research_corpus)
async def research_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any] | str | None:
    """Pass the LLM's research findings through; the corpus was injected
    by load_research_corpus so the model cites real sources. Lenient
    yields enforcement fills any missing declared keys."""
    return llm_response
