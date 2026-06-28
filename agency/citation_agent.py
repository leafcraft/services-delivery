"""citation_agent — mechanical citation resolution.

WHY THIS MUST STAY PYTHON: citation verification is a deterministic
CONTROL, not judgment — an LLM asserting "the citations look fine" is
exactly the failure mode this agent exists to catch. Set intersection
of in-text [cite: X] tags against the declared citations list, plus an
exact coverage ratio, must be computed mechanically and auditable.
Production swaps the dev heuristic for a real source lookup; the
return shape and downstream wires stay the same.
"""
from __future__ import annotations

import re
from typing import Any

from leafmesh import LeafMeshLogger

from agency._shared import store

logger = LeafMeshLogger(__name__)

CITE_RE = re.compile(r"\[cite:\s*([^\]]+)\]")
COVERAGE_FLOOR = 0.8


async def citation_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Programmatic citation check — counts how many cited claims have
    resolvable sources. Production: real source lookup; dev: heuristic."""
    citations = list(input_data.get("citations") or [])
    sections = list(input_data.get("sections") or [])

    # Collect all [cite: X] occurrences from section bodies
    in_text_cites: set[str] = set()
    for s in sections:
        if not isinstance(s, dict):
            continue
        body = str(s.get("body") or "")
        for m in CITE_RE.findall(body):
            in_text_cites.add(m.strip())

    # Declared citations
    declared: set[str] = set()
    for c in citations:
        if isinstance(c, dict):
            sid = str(c.get("source_id") or "").strip()
            if sid:
                declared.add(sid)
        elif isinstance(c, str):
            declared.add(c.strip())

    # A citation is verified only if it is BOTH declared AND resolves to a
    # retrievable source. Dev: the resolvable set is the seeded research
    # corpus; PROD: a real source lookup against your KB/RAG / archive.
    # (Unknown corpus → fall back to declared-only so the dev demo with a
    # synthetic draft still flows.)
    resolvable = store.corpus_source_ids()
    if resolvable:
        verified = {c for c in (in_text_cites & declared) if c in resolvable}
    else:
        verified = in_text_cites & declared
    missing = in_text_cites - verified

    total = len(in_text_cites)
    coverage = (len(verified) / total) if total > 0 else 1.0
    gaps_found = coverage < COVERAGE_FLOOR or len(missing) > 0

    logger.info(
        f"[citation] verified={len(verified)}/{total} coverage={coverage:.0%} "
        f"missing={len(missing)} gaps={gaps_found}"
    )
    return {
        "total_citations": total,
        "verified_citations": len(verified),
        "missing_sources": sorted(missing),
        "coverage_pct": round(coverage, 2),
        "citation_gaps_found": gaps_found,
    }
