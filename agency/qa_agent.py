"""qa_agent — deterministic QA floor + tier derivation + pre-gate stash.

This is the last agent before the human gates, and it carries three
things the LLM must NOT be able to waive — so they live in a `@chain`
floor that runs AFTER the model, not in the prompt:

  1. QA GATE (citation / quality floor) — `approved` is forced false
     whenever there are uncited claims, unresolved citations, or internal
     contradictions. No judgment call, no "minor exception".
  2. PII FLOOR — a deterministic regex scan over the section bodies for
     emails / phones / SSN-shaped strings. Any hit is merged into
     `pii_concerns` even if the model said the draft was clean — PII on
     client-facing material is a firm-level escalation.
  3. STAKES→TIER DERIVATION — `review_tier` (associate / senior / sme /
     partner) is computed in Python from the stakes (PII, practice area,
     deliverable type, strategic account, confidence). The visible
     can_call wires in the configuration route on the SAME fields; this floor
     guarantees the fields are correct before they are read.

@pre_compose  pull_engagement_record — recover the engagement/deliverable
              record (client, sensitivities, corpus source-ids) from the
              dev store so QA verifies citations against what is actually
              retrievable, not the LLM's say-so.
@chain        qa_floor — the three deterministic floors above, then the
              per-session pre-gate stash so a post-HITL kick-back
              (associate → research) can recover the engagement.

All citation/contradiction DETECTION still happens in the LLM prompt;
this floor only enforces the CONSEQUENCES the LLM cannot be trusted to
apply to itself.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store
from agency._shared.session_stash import resolve_session_id, stash_get, stash_put

logger = LeafMeshLogger(__name__)

# Practice areas that always demand a subject-matter expert.
SME_AREAS = frozenset({"regulated", "tax", "healthcare", "security", "legal"})
CONFIDENCE_FLOOR = 0.7

# Deterministic PII patterns — a backstop the LLM can't talk its way past.
# SSN is matched (and removed) FIRST so its dashed shape isn't double-counted
# as a phone number.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PII_PATTERNS = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("phone", re.compile(r"(?<!\d)(?:\+?\d[\d\-\s().]{8,}\d)(?!\d)")),
)

# Engagement + deliverable context worth recovering after a human gate.
_STASH_KEYS = (
    "engagement_id", "client_id", "practice_area", "deliverable_type",
    "scope_summary", "deadline_utc", "strategic_account",
    "deliverable_id", "sections", "confidence",
)


def _parse(resp: Any) -> dict[str, Any]:
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        try:
            return json.loads(resp)
        except (ValueError, TypeError):
            m = re.search(r"\{.*\}", resp, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except (ValueError, TypeError):
                    pass
    return {}


def _section_text(sections: Any) -> str:
    if not isinstance(sections, list):
        return ""
    parts: list[str] = []
    for s in sections:
        if isinstance(s, dict):
            parts.append(str(s.get("body") or ""))
        elif isinstance(s, str):
            parts.append(s)
    return "\n".join(parts)


def scan_pii(text: str) -> list[str]:
    """Deterministic PII scan — returns a concern string per pattern hit.
    SSN is matched first and its spans stripped so it doesn't also trip
    the phone matcher."""
    text = text or ""
    found: list[str] = []
    if _SSN_RE.search(text):
        found.append("pii:ssn")
        text = _SSN_RE.sub(" ", text)   # don't double-count as phone
    for label, rx in _PII_PATTERNS:
        if rx.search(text):
            found.append(f"pii:{label}")
    return found


def derive_review_tier(
    *,
    pii_concerns: list[Any],
    practice_area: str,
    deliverable_type: str,
    strategic_account: bool,
    confidence: float,
) -> str:
    """Stakes→tier derivation (the review matrix, in code).

    Precedence: PII → partner; regulated area OR shaky confidence → sme;
    recommendation OR strategic account → senior; else associate. The
    visible can_call wires route on the SAME fields — this is the
    authoritative computation behind them.
    """
    if pii_concerns:
        return "partner"
    pa = (practice_area or "").strip().lower()
    if pa in SME_AREAS or confidence < CONFIDENCE_FLOOR:
        return "sme"
    if (deliverable_type or "").strip().lower() == "recommendation" or strategic_account:
        return "senior"
    return "associate"


# ── @pre_compose: pull the engagement record QA verifies against ──────
def pull_engagement_record(data: Any, ctx: Any) -> dict[str, Any]:
    input_data = data if isinstance(data, dict) else {}
    eid = input_data.get("engagement_id", "")
    uy = input_data.get("upstream_yields")
    if not eid and isinstance(uy, dict):
        eid = uy.get("engagement_id") or ""
    brief = store.get_brief(eid) if eid else {}
    client_id = brief.get("client_id") or input_data.get("client_id") or ""
    account = store.account_context(client_id) if client_id else {}
    return {
        "engagement_id": eid,
        "brief": brief,
        "account": account,
        "sensitivities": account.get("sensitivities", []),
        "resolvable_source_ids": sorted(store.corpus_source_ids()),
    }


# ── @chain: the deterministic floor (runs AFTER the LLM) ──────────────
async def qa_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    result.setdefault("approved", False)   # default-deny if the LLM dropped it

    uncited = result.get("uncited_claims") or []
    bad_cites = result.get("bad_citations") or []
    contradictions = result.get("contradictions") or []

    # 1. QA GATE — quality/citation floor the LLM cannot waive.
    if uncited or bad_cites or contradictions:
        if result.get("approved") is True:
            logger.warning("[qa] floor OVERRODE approved=true — defects present")
        result["approved"] = False

    # 2. PII FLOOR — deterministic scan merged into the LLM's concerns.
    pii = list(result.get("pii_concerns") or [])
    scanned = scan_pii(_section_text(result.get("sections")))
    for hit in scanned:
        if hit not in pii:
            pii.append(hit)
    if scanned:
        result["approved"] = False        # PII never ships
        logger.warning(f"[qa] PII floor flagged {scanned} — forcing partner review")
    result["pii_concerns"] = pii

    # 3. STAKES→TIER DERIVATION — authoritative behind the visible wires.
    try:
        confidence = float(result.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    result["review_tier"] = derive_review_tier(
        pii_concerns=pii,
        practice_area=str(result.get("practice_area", "")),
        deliverable_type=str(result.get("deliverable_type", "")),
        strategic_account=bool(result.get("strategic_account", False)),
        confidence=confidence,
    )

    # Pre-gate stash so a post-HITL kick-back can recover the engagement.
    cdict = ctx if isinstance(ctx, dict) else {}
    session_id = resolve_session_id(result, cdict)
    payload = {k: result.get(k) for k in _STASH_KEYS if result.get(k) not in (None, "")}
    if session_id and payload:
        stash = stash_get(session_id)
        stash.update(payload)
        stash_put(session_id, stash)

    logger.info(
        f"[qa] approved={result['approved']} tier={result['review_tier']} "
        f"pii={len(pii)} uncited={len(uncited)} bad_cites={len(bad_cites)}"
    )
    return result


@pre_compose(context_processor=pull_engagement_record)
@chain(qa_floor)
async def qa_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Pass the LLM's QA judgment through; qa_floor enforces the floors."""
    parsed = _parse(llm_response)
    # Identity passthrough fallback from upstream when the model dropped it.
    if isinstance(input_data, dict):
        uy = input_data.get("upstream_yields")
        src = uy if isinstance(uy, dict) else input_data
        for k in ("deliverable_id", "sections", "practice_area",
                  "deliverable_type", "strategic_account", "confidence"):
            if parsed.get(k) in (None, "") and src.get(k) not in (None, ""):
                parsed[k] = src[k]
    return parsed
