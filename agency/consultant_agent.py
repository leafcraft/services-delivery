"""consultant_agent — self-reliant engagement consultant.

The consultant is the reasoning-heavy heart of the mesh: it analyses the
research + client context AND writes the first-pass deliverable. To do
that like a real consultant, it pulls its OWN context rather than relying
on a conductor to spoon-feed it.

@pre_compose  load_engagement_context — from the dev store (PROD: CRM +
              KB + deliverables archive) pull the BRIEF, the research
              CORPUS for the practice area, the client ACCOUNT context
              (stakeholders, open threads, sensitivities), PRIOR
              DELIVERABLES for reusable framing, and the ENGAGEMENT
              RECORD. The LLM reasons over real material on day 0.
@compose      per-downstream payloads — sme_human gets the analysis +
              the specific reason SME review is needed (not the whole
              draft); qa_agent gets the full draft + the tier-routing
              fields it derives the review tier from.

All analysis + drafting judgment stays in the YAML prompt (Claude +
thinking budget); this module only assembles inputs and shapes outputs.
The deterministic floors (confidence cap on evidence-free hypotheses,
deliverable_id synthesis) are enforced here so the LLM can't waive them.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from leafmesh import LeafMeshLogger, compose, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

CONFIDENCE_FLOOR = 0.7
NO_EVIDENCE_CONFIDENCE_CAP = 0.5

_DEFAULTS: dict[str, Any] = {
    "hypothesis": "", "supporting_evidence": [], "counter_considerations": [],
    "open_questions": [], "confidence": 0.0, "requires_sme_review": False,
    "executive_takeaway": "", "deliverable_id": "", "sections": [],
    "citations": [], "practice_area": "", "deliverable_type": "",
    "strategic_account": False,
}


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


def _pick(data: Any, key: str, default: Any = "") -> Any:
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


# ── @pre_compose: the consultant pulls its own context ────────────────
def load_engagement_context(data: Any, ctx: Any) -> dict[str, Any]:
    input_data = data if isinstance(data, dict) else {}
    eid = _pick(input_data, "engagement_id", "") or store.default_engagement_id()
    brief = store.get_brief(eid)
    client_id = brief.get("client_id") or _pick(input_data, "client_id", "")
    practice_area = brief.get("practice_area") or _pick(input_data, "practice_area", "")
    account = store.account_context(client_id) if client_id else {}
    corpus = store.research_corpus(practice_area)
    priors = store.prior_deliverables(client_id) if client_id else []
    logger.info(
        f"[consultant] pre_compose eng={eid or '-'} client={client_id or '-'} "
        f"corpus={len(corpus)} priors={len(priors)}"
    )
    return {
        "engagement_id": eid,
        "brief": brief,
        "account": account,
        "stakeholders": account.get("stakeholders", []),
        "open_threads": account.get("open_threads", []),
        "sensitivities": account.get("sensitivities", []),
        "research_corpus": corpus,
        "prior_deliverables": priors,
        # echoed for the tier-routing passthrough below
        "practice_area": str(practice_area or "").lower(),
        "deliverable_type": brief.get("deliverable_type", ""),
        "strategic_account": bool(brief.get("strategic_account", False)),
    }


# ── @compose: different payloads per downstream ───────────────────────
def _to_sme(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """SME gets the analysis + WHY review is needed — not the whole draft."""
    reason = "low confidence" if result.get("confidence", 1.0) < CONFIDENCE_FLOOR else \
             ("no supporting evidence" if not result.get("supporting_evidence") else
              "consultant flagged SME review")
    return {
        "deliverable_id": result.get("deliverable_id", ""),
        "practice_area": result.get("practice_area", ""),
        "hypothesis": result.get("hypothesis", ""),
        "supporting_evidence": result.get("supporting_evidence", []),
        "counter_considerations": result.get("counter_considerations", []),
        "confidence": result.get("confidence", 0.0),
        "sme_review_reason": reason,
    }


def _to_qa(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """QA gets the full draft + the tier-routing fields it derives on."""
    return {
        "engagement_id": result.get("engagement_id", ""),
        "deliverable_id": result.get("deliverable_id", ""),
        "sections": result.get("sections", []),
        "citations": result.get("citations", []),
        "practice_area": result.get("practice_area", ""),
        "deliverable_type": result.get("deliverable_type", ""),
        "strategic_account": result.get("strategic_account", False),
        "confidence": result.get("confidence", 0.0),
    }


@pre_compose(context_processor=load_engagement_context)
@compose(sme_human=_to_sme, qa_agent=_to_qa)
async def consultant_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    out = {**_DEFAULTS}
    parsed = _parse(llm_response)
    out.update({k: parsed[k] for k in _DEFAULTS if k in parsed})

    prepared = {}
    if isinstance(context, dict) and isinstance(context.get("prepared_data"), dict):
        prepared = context["prepared_data"]
    pctx = prepared.get("context", prepared) if isinstance(prepared, dict) else {}

    # Tier-routing passthrough from the pulled brief (authoritative).
    for k in ("engagement_id", "practice_area", "deliverable_type", "strategic_account"):
        if not out.get(k) and pctx.get(k) not in (None, ""):
            out[k] = pctx[k]

    # Deterministic floors the LLM cannot waive.
    try:
        conf = float(out.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if out.get("hypothesis") and not out.get("supporting_evidence"):
        conf = min(conf, NO_EVIDENCE_CONFIDENCE_CAP)   # evidence-free poison
        out["requires_sme_review"] = True
    if conf < CONFIDENCE_FLOOR:
        out["requires_sme_review"] = True
    out["confidence"] = round(max(0.0, min(1.0, conf)), 2)

    if not out.get("deliverable_id"):
        out["deliverable_id"] = f"del-{uuid.uuid4().hex[:12]}"

    logger.info(
        f"[consultant] conf={out['confidence']} sme={out['requires_sme_review']} "
        f"sections={len(out.get('sections') or [])} del={out['deliverable_id']}"
    )
    return out
