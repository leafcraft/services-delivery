"""performance_review_agent — deterministic rating floor on an HR decision.

The review NARRATIVE (the prose, the cited engagements, the client
quotes) is genuine LLM work and stays in the YAML prompt. But the
RATING and its consequences — partner calibration, PIP, managed exit —
are an HR-consequential decision driven by a fixed weighted formula and
fixed bands. An LLM nudging a composite score by 0.03 flips someone into
or out of a managed exit; that decision must be deterministic and
LLM-unwaivable, not a model's arithmetic. So the scoring + banding +
flag derivation live in a `@chain` floor.

@chain  rating_floor — runs AFTER the LLM and:
        1. RECOMPUTES client_csat_avg from the raw feedback (mean of the
           csat values), composite_score from the weighted formula, and
           the rating band — overriding whatever the model wrote.
        2. DERIVES the consequence flags (partner_review / calibration /
           pip / managed_exit) from the rating, deterministically, so a
           given rating ALWAYS triggers the right human path.
        3. Sets escalation_reason when partner review is required.

The visible can_call wires route on the SAME flags — this floor makes
the flags trustworthy before they are read.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from leafmesh import LeafMeshLogger, chain

logger = LeafMeshLogger(__name__)


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


def _csat_values(input_data: Any, result: dict[str, Any]) -> list[float]:
    """Collect csat numbers from the client-feedback input (PROD: your
    feedback/CRM system). Falls back to the LLM's own average."""
    vals: list[float] = []

    def _scan(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "csat" and isinstance(v, (int, float)):
                    vals.append(float(v))
                else:
                    _scan(v)
        elif isinstance(obj, list):
            for it in obj:
                _scan(it)

    if isinstance(input_data, dict):
        _scan(input_data.get("client_feedback"))
        if not vals:
            _scan(input_data.get("upstream_yields"))
    return vals


def composite_score(
    *, csat: float, repeat_pct: float, outcomes: float, util_pct: float
) -> float:
    """The documented weighted formula, in code (csat normalised 0..1 on a
    5-point scale)."""
    csat_norm = csat / 5.0 if csat > 1.0 else csat
    s = (
        0.40 * csat_norm
        + 0.20 * repeat_pct
        + 0.20 * min(1.0, outcomes / 3.0)
        + 0.20 * min(1.0, util_pct / 75.0)
    )
    return round(min(1.0, max(0.0, s)), 2)


def rating_band(score: float) -> str:
    if score >= 0.90:
        return "principal_track"
    if score >= 0.75:
        return "exceeds"
    if score >= 0.60:
        return "meets"
    if score >= 0.40:
        return "improvement_plan"
    return "managed_exit"


def consequence_flags(rating: str) -> dict[str, bool]:
    """Rating → human-path flags, deterministically."""
    return {
        "requires_partner_review": rating == "principal_track",
        "requires_calibration": rating in ("principal_track", "managed_exit"),
        "requires_pip": rating == "improvement_plan",
        "requires_managed_exit": rating == "managed_exit",
    }


# ── @chain: the deterministic rating floor (runs AFTER the LLM) ───────
async def rating_floor(result: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    input_data = {}
    if isinstance(ctx, dict):
        input_data = ctx.get("input_data") or ctx.get("input") or {}

    consultant_id = str(result.get("consultant_id", "") or "")
    period = str(result.get("period", "") or "")
    if not result.get("review_id"):
        result["review_id"] = f"svc-review-{consultant_id}-{period}"

    # 1. RECOMPUTE the score inputs the LLM must not invent.
    csat_vals = _csat_values(input_data, result)
    csat_avg = (
        round(sum(csat_vals) / len(csat_vals), 2) if csat_vals
        else round(_num(result.get("client_csat_avg")), 2)
    )
    result["client_csat_avg"] = csat_avg

    score = composite_score(
        csat=csat_avg,
        repeat_pct=_num(result.get("repeat_engagement_pct")),
        outcomes=_num(result.get("outcomes_delivered_count")),
        util_pct=_num(result.get("utilisation_pct")),
    )
    result["composite_score"] = score

    rating = rating_band(score)
    if result.get("rating") and result.get("rating") != rating:
        logger.warning(
            f"[review] rating floor overrode '{result.get('rating')}' → "
            f"'{rating}' (composite {score})"
        )
    result["rating"] = rating

    # 2. DERIVE the consequence flags from the rating — LLM cannot waive.
    flags = consequence_flags(rating)
    result.update(flags)

    # 3. escalation_reason for the partner path.
    if flags["requires_partner_review"]:
        result["escalation_reason"] = (
            f"consultant {consultant_id} rated {rating} (composite {score}) "
            f"— partner calibration required"
        )
    else:
        result.setdefault("escalation_reason", "")
        if not result.get("escalation_reason"):
            result["escalation_reason"] = ""

    if not result.get("generated_at_utc"):
        result["generated_at_utc"] = datetime.now(timezone.utc).isoformat()

    logger.info(
        f"[review] {result['review_id']} composite={score} rating={rating} "
        f"partner={flags['requires_partner_review']} pip={flags['requires_pip']} "
        f"exit={flags['requires_managed_exit']}"
    )
    return result


@chain(rating_floor)
async def performance_review_agent(
    llm_response: dict[str, Any] | str | None,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Pass the LLM's review narrative through; rating_floor recomputes the
    score, the band and the consequence flags deterministically. Carry
    consultant_id / period forward from input when the model dropped them."""
    parsed = _parse(llm_response)
    if isinstance(input_data, dict):
        for k in ("consultant_id", "period", "engagement_id"):
            if parsed.get(k) in (None, "") and input_data.get(k) not in (None, ""):
                parsed[k] = input_data[k]
    return parsed
