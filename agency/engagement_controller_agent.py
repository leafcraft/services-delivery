"""engagement_controller_agent — deterministic SOW-control floor.

The controller's JUDGMENT (which activities are out of scope, which
client requests are change-order candidates, the lead briefing) is real
reasoning and stays in the YAML prompt. But the CONTROL DECISION — does
this engagement need the lead to act? — must NOT be something the LLM can
talk itself out of. Budget burn, scope creep and under-utilisation are
exactly the failures a model under-flags ("probably fine"); on billable
work that silently eats margin. So the escalation floor lives in a
`@chain` the LLM cannot waive.

@chain  control_floor — runs AFTER the LLM and:
        1. RECOMPUTES severity_score from the PSA-supplied numbers with
           the documented formula (the LLM does not get to invent it).
        2. FORCES requires_lead_action = true whenever ANY hard control
           trips — over budget, budget burn >= the threshold, a
           change-order candidate exists, or utilisation has collapsed —
           regardless of what the model concluded. Deterministic floor.

Identity passthrough of the PSA numbers (the system of record) is also
backstopped here so the lead briefing is computed on real figures.
"""
from __future__ import annotations

import json
import re
from typing import Any

from leafmesh import LeafMeshLogger, chain

logger = LeafMeshLogger(__name__)

# Control thresholds — the deterministic floor. Tune per firm.
BUDGET_BURN_FLOOR = 0.90          # >= 90% of SOW budget consumed → lead acts
UTILISATION_FLOOR = 0.50         # billable share collapsed → lead acts

# PSA fields the controller must not invent — echoed from the connector.
_PSA_FIELDS = (
    "engagement_id", "total_billable_hours", "total_non_billable_hours",
    "total_billable_cents", "utilisation_pct", "entries_count",
    "budget_utilisation", "over_budget",
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


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_severity(
    *, budget_utilisation: float, scope_drift_count: int, change_order_count: int
) -> float:
    """severity_score (0..1), the documented formula in code."""
    s = (
        min(0.5, max(0.0, budget_utilisation - 0.85))
        + min(0.3, 0.1 * scope_drift_count)
        + min(0.2, 0.1 * change_order_count)
    )
    return round(min(1.0, max(0.0, s)), 2)


def control_trips(
    *,
    over_budget: bool,
    budget_utilisation: float,
    utilisation_pct: float,
    change_order_count: int,
    scope_drift_count: int,
) -> list[str]:
    """Every hard control that fired — non-empty means the lead must act."""
    trips: list[str] = []
    if over_budget:
        trips.append("over_budget")
    if budget_utilisation >= BUDGET_BURN_FLOOR:
        trips.append("budget_burn")
    if change_order_count > 0:
        trips.append("change_order_candidate")
    if scope_drift_count > 0:
        trips.append("scope_drift")
    # Utilisation collapse only matters once there's logged time.
    if 0.0 < utilisation_pct < UTILISATION_FLOOR:
        trips.append("low_utilisation")
    return trips


# ── @chain: the deterministic control floor (runs AFTER the LLM) ──────
async def control_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}

    drift = result.get("scope_drift_items") or []
    change_orders = result.get("change_order_candidates") or []
    drift_n = len(drift) if isinstance(drift, list) else 0
    co_n = len(change_orders) if isinstance(change_orders, list) else 0

    budget_util = _num(result.get("budget_utilisation"))
    util_pct = _num(result.get("utilisation_pct"))
    over_budget = bool(result.get("over_budget"))

    # 1. RECOMPUTE severity — the LLM does not get to set it.
    result["severity_score"] = compute_severity(
        budget_utilisation=budget_util,
        scope_drift_count=drift_n,
        change_order_count=co_n,
    )

    # 2. FORCE escalation on any tripped control — the LLM can't waive it.
    trips = control_trips(
        over_budget=over_budget,
        budget_utilisation=budget_util,
        utilisation_pct=util_pct,
        change_order_count=co_n,
        scope_drift_count=drift_n,
    )
    forced = bool(trips)
    if forced and result.get("requires_lead_action") is not True:
        logger.warning(
            f"[controller] control floor forcing lead action — trips={trips} "
            f"(LLM said requires_lead_action={result.get('requires_lead_action')})"
        )
    result["requires_lead_action"] = bool(result.get("requires_lead_action")) or forced
    result["control_trips"] = trips

    if not result.get("scope_briefing"):
        result["scope_briefing"] = (
            "On budget. No drift items. No action this cycle."
            if not forced else
            f"Controls tripped: {', '.join(trips)}. Lead review required."
        )

    logger.info(
        f"[controller] eng={result.get('engagement_id') or '-'} "
        f"severity={result['severity_score']} trips={trips} "
        f"lead_action={result['requires_lead_action']}"
    )
    return result


@chain(control_floor)
async def engagement_controller_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Pass the LLM's scope judgment through; control_floor recomputes
    severity and enforces the escalation floor. Backstop the PSA-number
    passthrough from upstream when the model dropped it (the PSA is the
    system of record — never recompute, only carry forward)."""
    parsed = _parse(llm_response)
    if isinstance(input_data, dict):
        uy = input_data.get("upstream_yields")
        src = uy if isinstance(uy, dict) else input_data
        if isinstance(src, dict):
            for k in _PSA_FIELDS:
                if parsed.get(k) in (None, "") and src.get(k) not in (None, ""):
                    parsed[k] = src[k]
    return parsed
