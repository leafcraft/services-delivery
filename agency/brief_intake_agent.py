"""brief_intake_agent — self-reliant intake + deterministic completeness floor.

This is the front door of the mesh. Two responsibilities, two decorators:

@pre_compose  load_prior_engagement — if the inbound brief references an
              engagement we already know (a follow-on SOW, a re-brief),
              pull the PRIOR brief from the dev store (PROD: your CRM /
              PSA) so intake normalises ON TOP of what we already hold
              instead of starting cold. Self-reliant: it fetches its own
              prior context rather than waiting for a conductor.

@chain        completeness_floor — a deterministic floor the LLM cannot
              waive. A brief missing SCOPE, STAKES (deliverable_type) or
              the DEADLINE is not workable: routed downstream it produces
              a confident-but-wrong deliverable on the wrong thing by the
              wrong date — the most expensive failure in services work.
              The floor forces `requires_human_clarification = true` and
              lists the `missing_fields`; the engagement lead clarifies
              with the client BEFORE any research/context spend. The LLM
              may flag incompleteness in its prompt, but it cannot SUPPRESS
              this — the Python recomputes it from the canonical fields.

All brief NORMALISATION (parsing the email/SOW/transcript into the
canonical shape, the policy floors on research) stays in the YAML prompt.
This module pulls prior context and enforces the workability floor.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

# A brief is not workable without all three: WHAT (scope), WHAT-SHAPE
# (deliverable_type = the stakes/effort signal) and WHEN (deadline).
_REQUIRED_FIELDS = ("scope_summary", "deliverable_type", "deadline_utc")


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
    bp = data.get("brief_payload")
    if isinstance(bp, dict) and bp.get(key) not in (None, ""):
        return bp[key]
    return default


# ── @pre_compose: pull any prior engagement/brief to normalise on top ─
def load_prior_engagement(data: Any, ctx: Any) -> dict[str, Any]:
    input_data = data if isinstance(data, dict) else {}
    eid = _pick(input_data, "engagement_id", "")
    prior = store.get_brief(eid) if eid else {}
    logger.info(
        f"[brief_intake] pre_compose eng={eid or '-'} "
        f"prior_brief={'yes' if prior else 'no'}"
    )
    return {
        "engagement_id": eid,
        "prior_brief": prior,
        # Surface the prior canonical fields so the LLM can carry them
        # forward rather than re-deriving (and losing) them.
        "prior_client_id": prior.get("client_id", ""),
        "prior_practice_area": prior.get("practice_area", ""),
        "prior_scope_summary": prior.get("scope_summary", ""),
    }


def missing_required_fields(brief: dict[str, Any]) -> list[str]:
    """Deterministic completeness check — the workability floor, in code."""
    if not isinstance(brief, dict):
        return list(_REQUIRED_FIELDS)
    missing: list[str] = []
    for f in _REQUIRED_FIELDS:
        v = brief.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(f)
    return missing


# ── @chain: the completeness floor (runs AFTER the LLM) ───────────────
async def completeness_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}

    if not result.get("engagement_id"):
        result["engagement_id"] = f"eng-{uuid.uuid4().hex[:12]}"

    missing = missing_required_fields(result)
    result["missing_fields"] = missing
    incomplete = bool(missing)

    # The LLM cannot waive this: an incomplete brief ALWAYS goes to the
    # lead for clarification, never downstream to research/context.
    if incomplete and result.get("requires_human_clarification") is not True:
        logger.warning(
            f"[brief_intake] completeness floor: missing {missing} — "
            f"forcing human clarification (LLM said "
            f"{result.get('requires_human_clarification')})"
        )
    result["requires_human_clarification"] = incomplete

    # An incomplete brief must NOT trigger a research spend.
    if incomplete:
        result["requires_research"] = False

    logger.info(
        f"[brief_intake] eng={result['engagement_id']} "
        f"incomplete={incomplete} missing={missing} "
        f"requires_research={result.get('requires_research')}"
    )
    return result


@pre_compose(context_processor=load_prior_engagement)
@chain(completeness_floor)
async def brief_intake_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Pass the LLM's normalised brief through; completeness_floor enforces
    the workability floor. Carry the engagement_id forward from input when
    the model dropped it."""
    parsed = _parse(llm_response)
    if not parsed.get("engagement_id") and isinstance(input_data, dict):
        eid = _pick(input_data, "engagement_id", "")
        if eid:
            parsed["engagement_id"] = eid
    return parsed
