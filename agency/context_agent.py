"""context_agent — self-reliant account context + research↔context loop guard.

Two responsibilities, two decorators:

@pre_compose  load_account_context — the context analyst pulls the
              CLIENT/ACCOUNT context it reasons over (prior engagements,
              stakeholder map, open threads, sensitivities) from the dev
              store (PROD: your CRM — Salesforce/HubSpot) BEFORE it runs,
              instead of being spoon-fed by a conductor. A prompt that
              just says "pull client history" has nothing on day 0; this
              injects the real account so the LLM reasons over it.

@chain        context_agent — the research↔context cross-reference is an
              intentional peer-to-peer cycle, bounded to ONE bounce per
              session. The bound needs a per-session counter that persists
              across the two agent invocations — `can_call` conditions are
              stateless expressions over the current response, so a "have
              we already bounced?" memory cannot be expressed in YAML. The
              module reads/increments the bounce counter in the session
              stash and overwrites `context_retry_exhausted` on the LLM's
              output; the rest passes through untouched (lenient yields
              enforcement fills any missing keys).

All client-history JUDGMENT (which threads matter, the clamps) lives in
the YAML prompt — this module only assembles inputs and enforces the
bound the LLM can't waive.
"""
from __future__ import annotations

import json
from typing import Any

from leafmesh import LeafMeshLogger, pre_compose

from agency._shared import store
from agency._shared.session_stash import resolve_session_id, stash_get, stash_put

logger = LeafMeshLogger(__name__)


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


# ── @pre_compose: the context analyst pulls its own account context ───
def load_account_context(data: Any, ctx: Any) -> dict[str, Any]:
    input_data = data if isinstance(data, dict) else {}
    eid = _pick(input_data, "engagement_id", "") or store.default_engagement_id()
    brief = store.get_brief(eid)
    client_id = brief.get("client_id") or _pick(input_data, "client_id", "")
    account = store.account_context(client_id) if client_id else {}
    logger.info(
        f"[context] pre_compose eng={eid or '-'} client={client_id or '-'} "
        f"prior_engagements={len(account.get('prior_engagements', []))} "
        f"stakeholders={len(account.get('stakeholders', []))}"
    )
    return {
        "engagement_id": eid,
        "client_id": client_id,
        "account": account,
        "prior_engagements": account.get("prior_engagements", []),
        "stakeholders": account.get("stakeholders", []),
        "open_threads": account.get("open_threads", []),
        "sensitivities": account.get("sensitivities", []),
    }


@pre_compose(context_processor=load_account_context)
async def context_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(llm_response, str):
        try:
            llm_response = json.loads(llm_response)
        except (ValueError, TypeError):
            llm_response = {}
    if not isinstance(llm_response, dict):
        llm_response = {}

    try:
        confidence = float(llm_response.get("confidence", 0.5) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.5

    # Loop guard: one context→research bounce per session, then proceed
    # to fan-in instead of ping-ponging until the chain cap.
    session_id = resolve_session_id(input_data, context)
    stash = stash_get(session_id)
    bounces = int(stash.get("context_research_bounces") or 0)
    exhausted = bounces >= 1
    if confidence < 0.6 and not exhausted:
        stash["context_research_bounces"] = bounces + 1
        stash_put(session_id, stash)
    elif confidence < 0.6 and exhausted:
        logger.warning(
            "[context] low confidence persists after a research bounce — "
            "proceeding to fan-in instead of looping"
        )

    return {**llm_response, "context_retry_exhausted": exhausted}
