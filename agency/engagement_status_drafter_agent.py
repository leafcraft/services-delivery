"""engagement_status_drafter_agent — self-reliant client-comms drafter.

Writing the weekly client update is the consulting chore this agent
deflects. Given only an engagement_id (or nothing — the cron fires with
no payload), it pulls its OWN engagement record so the draft has real
content on day 0.

@pre_compose  load_engagement_record — from the dev store (PROD: your
              PSA + CRM) pull the BRIEF, the client ACCOUNT open threads
              and stakeholders, and PRIOR DELIVERABLES, so the status
              report is grounded in the actual engagement rather than an
              empty session.

All status-writing judgment (tone, lead-with-outcomes, the empty-week
floor) stays in the YAML prompt; this module only assembles the record
and applies the deterministic empty-week confidence cap the LLM can't
waive.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, pre_compose

from agency._shared import store

logger = LeafMeshLogger(__name__)

_TONES = frozenset({"warm", "direct", "technical"})
EMPTY_WEEK_CONFIDENCE_CAP = 0.3

_DEFAULTS: dict[str, Any] = {
    "client_facing_summary": "", "this_week_accomplishments": [],
    "blockers_for_client": [], "next_week_plan": [], "open_questions": [],
    "confidence_in_timeline": 0.0, "tone_used": "warm",
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


def load_engagement_record(data: Any, ctx: Any) -> dict[str, Any]:
    input_data = data if isinstance(data, dict) else {}
    eid = input_data.get("engagement_id", "") or store.default_engagement_id()
    brief = store.get_brief(eid)
    client_id = brief.get("client_id", "")
    account = store.account_context(client_id) if client_id else {}
    logger.info(f"[status] pre_compose eng={eid or '-'} client={client_id or '-'}")
    return {
        "engagement_id": eid,
        "brief": brief,
        "client_name": brief.get("client_name", ""),
        "open_threads": account.get("open_threads", []),
        "stakeholders": account.get("stakeholders", []),
        "prior_deliverables": store.prior_deliverables(client_id) if client_id else [],
    }


@pre_compose(context_processor=load_engagement_record)
async def engagement_status_drafter_agent(llm_response: Any, input_data: Any, context: Any) -> dict[str, Any]:
    out = {**_DEFAULTS}
    parsed = _parse(llm_response)
    out.update({k: parsed[k] for k in _DEFAULTS if k in parsed})

    # tone floor
    if str(out.get("tone_used", "")).strip().lower() not in _TONES:
        out["tone_used"] = "warm"

    # clamp + empty-week floor
    try:
        conf = float(out.get("confidence_in_timeline", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    no_content = not out.get("this_week_accomplishments") and not str(out.get("client_facing_summary", "")).strip()
    if no_content:
        conf = min(conf, EMPTY_WEEK_CONFIDENCE_CAP)
    out["confidence_in_timeline"] = round(conf, 2)

    logger.info(f"[status] tone={out['tone_used']} conf={out['confidence_in_timeline']} empty_week={no_content}")
    return out
