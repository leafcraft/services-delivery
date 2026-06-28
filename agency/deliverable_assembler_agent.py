"""deliverable_assembler_agent — the real artifact-producing executor.

WHY THIS MUST BE PYTHON (an actuator with a hard control floor):
the rest of the mesh drafts, fact-checks and routes — but nothing
FINISHES the job. This agent does: after the engagement lead approves,
it COMPILES the approved analysis + citations + exhibits into a formatted
client deliverable and renders it to a REAL file on disk
(./out/<deliverable>.md, plus .pdf when reportlab is installed). That is
a side-effect, not judgment — it belongs in code, not an LLM prompt.

The compile is FAIL-CLOSED. A deliverable is only ever written when BOTH
of these hold (enforced in the @chain floor the LLM/human cannot waive):

  1. The engagement lead APPROVED it for the client
     (human_message == 'approved_for_client'), and
  2. QA passed — no uncited claims, no unresolved citations, no
     contradictions, no PII concerns.

Anything else (needs_rework, hold_for_review, a QA defect, PII) is
blocked: no file is produced and the agent returns a blocked status with
the reasons. This is the completeness floor for the whole template — it
turns draft-and-route into a team that ships.

@pre_compose  recover_approved_deliverable — after the HITL gate the SDK
              re-delivers only the human-response meta, so this pulls the
              approved deliverable context (sections, citations,
              deliverable_id, engagement metadata) back from the per-
              session stash that qa_agent wrote, plus the brief/account
              from the dev store (PROD: your CRM + deliverables archive).
@chain        gate_and_render — the fail-closed approval+QA floor, then
              the real file render and the deliverables-archive write.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from leafmesh import LeafMeshLogger, chain, pre_compose

from agency._shared import store
from agency._shared.session_stash import resolve_session_id, stash_get

logger = LeafMeshLogger(__name__)

# Real artifacts land here. gitignored — see ./gitignore (./out/).
OUT_DIR = Path("./out")

# The engagement lead's approve verdict — the ONLY message that may ship.
APPROVE_MESSAGE = "approved_for_client"

# Re-used from the QA floor: the lists whose non-emptiness means QA failed.
_QA_DEFECT_KEYS = ("uncited_claims", "bad_citations", "contradictions", "pii_concerns")


# ── parsing helpers ───────────────────────────────────────────────────
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


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "deliverable"


def _human_message(input_data: dict[str, Any], context: dict[str, Any]) -> str:
    """The engagement lead's verdict. After a HITL gate the verdict arrives
    as human_message (top-level or under original_webhook_data)."""
    for src in (input_data, context):
        if not isinstance(src, dict):
            continue
        msg = src.get("human_message")
        if msg:
            return str(msg).strip().lower()
        owd = src.get("original_webhook_data")
        if isinstance(owd, dict) and owd.get("human_message"):
            return str(owd["human_message"]).strip().lower()
    return ""


# ── @pre_compose: recover the approved deliverable after the HITL gate ──
def recover_approved_deliverable(data: Any, ctx: Any) -> dict[str, Any]:
    """The lead approves in the Studio inbox; the SDK then resumes the
    chain with only the human-response meta — the deliverable that flowed
    through QA is NOT re-delivered. Recover it from the per-session stash
    qa_agent wrote, fall back to the engagement record in the dev store."""
    input_data = data if isinstance(data, dict) else {}
    cdict = ctx if isinstance(ctx, dict) else {}

    session_id = resolve_session_id(input_data, cdict)
    stash = stash_get(session_id) if session_id else {}

    # Anything explicitly present on the input wins over the stash (direct
    # calls / smoke tests pass the deliverable inline).
    uy = input_data.get("upstream_yields") if isinstance(input_data.get("upstream_yields"), dict) else {}

    def pick(key: str, default: Any) -> Any:
        for src in (input_data, uy, stash):
            if isinstance(src, dict) and src.get(key) not in (None, "", [], {}):
                return src[key]
        return default

    eid = pick("engagement_id", "") or store.default_engagement_id()
    brief = store.get_brief(eid) if eid else {}
    client_id = brief.get("client_id") or pick("client_id", "")
    account = store.account_context(client_id) if client_id else {}

    return {
        "session_id": session_id,
        "engagement_id": eid,
        "deliverable_id": pick("deliverable_id", ""),
        "sections": pick("sections", []),
        "citations": pick("citations", []),
        "deliverable_type": pick("deliverable_type", "") or brief.get("deliverable_type", ""),
        "executive_takeaway": pick("executive_takeaway", ""),
        "open_questions": pick("open_questions", []),
        "confidence": pick("confidence", 0.0),
        # QA verdict carried through so the floor can fail closed on a
        # defective draft even if the lead clicked approve.
        "qa_approved": pick("qa_approved", pick("approved", None)),
        "uncited_claims": pick("uncited_claims", []),
        "bad_citations": pick("bad_citations", []),
        "contradictions": pick("contradictions", []),
        "pii_concerns": pick("pii_concerns", []),
        "brief": brief,
        "client_name": brief.get("client_name") or account.get("client_name", ""),
        "client_id": client_id,
    }


# ── the real render ───────────────────────────────────────────────────
def _section_lines(sections: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(sections, list):
        for i, s in enumerate(sections, 1):
            if isinstance(s, dict):
                out.append((str(s.get("title") or f"Section {i}"), str(s.get("body") or "")))
            elif isinstance(s, str):
                out.append((f"Section {i}", s))
    return out


def _render_markdown(ctx: dict[str, Any]) -> str:
    client = ctx.get("client_name") or ctx.get("client_id") or "Client"
    dtype = (ctx.get("deliverable_type") or "deliverable").title()
    title = f"{client} — {dtype}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"*Engagement {ctx.get('engagement_id', '')} · Deliverable "
                 f"{ctx.get('deliverable_id', '')} · Compiled {now}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Executive summary
    summary = ctx.get("executive_takeaway") or ""
    if summary:
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(summary)
        lines.append("")

    # Body sections
    for title_s, body in _section_lines(ctx.get("sections")):
        lines.append(f"## {title_s}")
        lines.append("")
        lines.append(body)
        lines.append("")

    # Cited sources
    citations = ctx.get("citations") or []
    if citations:
        lines.append("## Cited Sources")
        lines.append("")
        seen: set[str] = set()
        for c in citations:
            sid = c.get("source_id") if isinstance(c, dict) else str(c)
            sid = str(sid or "").strip()
            if not sid or sid in seen:
                continue
            seen.add(sid)
            src = store.get_source(sid) if hasattr(store, "get_source") else {}
            label = src.get("topic") or src.get("summary") or ""
            lines.append(f"- `{sid}`" + (f" — {label}" if label else ""))
        lines.append("")

    # Appendix — open questions
    open_q = ctx.get("open_questions") or []
    if open_q:
        lines.append("## Appendix — Open Questions")
        lines.append("")
        for q in open_q:
            lines.append(f"- {q if isinstance(q, str) else json.dumps(q)}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Reviewed and approved by the engagement lead before release.*")
    lines.append("")
    return "\n".join(lines)


def _render_pdf(md_text: str, pdf_path: Path, ctx: dict[str, Any]) -> bool:
    """Best-effort PDF render. Returns True on success, False if reportlab
    is unavailable (the .md is always produced regardless)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    except ImportError:
        return False

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=18, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13, spaceBefore=8)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=14)

    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm)
    flow = []
    for raw in md_text.splitlines():
        ln = raw.rstrip()
        if not ln:
            flow.append(Spacer(1, 4))
            continue
        esc = ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if ln.startswith("# "):
            flow.append(Paragraph(esc[2:], h1))
        elif ln.startswith("## "):
            flow.append(Paragraph(esc[3:], h2))
        elif ln.startswith("---"):
            flow.append(Spacer(1, 6))
        else:
            flow.append(Paragraph(esc, body))
    doc.build(flow)
    return True


def compile_deliverable(ctx: dict[str, Any]) -> dict[str, Any]:
    """Render the approved deliverable to a real file. Returns artifact
    paths + a summary. Shared by the @chain floor and the smoke test."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    did = ctx.get("deliverable_id") or store.new_id("del")
    stem = f"{_slug(ctx.get('client_name') or ctx.get('client_id') or 'client')}-{did}"

    md_text = _render_markdown(ctx)
    md_path = OUT_DIR / f"{stem}.md"
    md_path.write_text(md_text, encoding="utf-8")

    artifacts = [str(md_path)]
    pdf_path = OUT_DIR / f"{stem}.pdf"
    pdf_ok = False
    try:
        pdf_ok = _render_pdf(md_text, pdf_path, ctx)
        if pdf_ok:
            artifacts.append(str(pdf_path))
    except Exception as e:                       # pragma: no cover - render hardening
        logger.warning(f"[assembler] PDF render failed (md still produced): {e}")

    # Persist to the deliverables archive (PROD: your archive system).
    try:
        store.put_deliverable({
            "deliverable_id": did,
            "client_id": ctx.get("client_id", ""),
            "engagement_id": ctx.get("engagement_id", ""),
            "deliverable_type": ctx.get("deliverable_type", ""),
            "title": f"{ctx.get('client_name') or 'Client'} — "
                     f"{(ctx.get('deliverable_type') or 'deliverable').title()}",
            "artifact_path": str(md_path),
            "status": "compiled",
        })
    except Exception as e:                       # pragma: no cover
        logger.warning(f"[assembler] deliverables-archive write failed: {e}")

    n_sections = len(_section_lines(ctx.get("sections")))
    n_cites = len({(c.get("source_id") if isinstance(c, dict) else str(c))
                   for c in (ctx.get("citations") or [])})
    summary = (f"Compiled '{ctx.get('deliverable_type') or 'deliverable'}' for "
               f"{ctx.get('client_name') or ctx.get('client_id') or 'client'} — "
               f"{n_sections} section(s), {n_cites} cited source(s).")

    logger.info(f"[assembler] WROTE {md_path}" + (f" + {pdf_path}" if pdf_ok else ""))
    return {
        "status": "compiled",
        "blocked": False,
        "deliverable_id": did,
        "artifact_path": str(md_path),
        "artifact_paths": artifacts,
        "pdf_rendered": pdf_ok,
        "section_count": n_sections,
        "citation_count": n_cites,
        "compiled_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "block_reasons": [],
        "engagement_id": ctx.get("engagement_id", ""),
    }


def _blocked(reasons: list[str], ctx: dict[str, Any]) -> dict[str, Any]:
    logger.warning(f"[assembler] BLOCKED — {reasons}; no deliverable produced")
    return {
        "status": "blocked",
        "blocked": True,
        "deliverable_id": ctx.get("deliverable_id", ""),
        "artifact_path": "",
        "artifact_paths": [],
        "pdf_rendered": False,
        "section_count": 0,
        "citation_count": 0,
        "compiled_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": "Deliverable NOT compiled — " + "; ".join(reasons),
        "block_reasons": reasons,
        "engagement_id": ctx.get("engagement_id", ""),
    }


# ── @chain: the fail-closed approval + QA floor, then the render ────────
def gate_and_render(result: dict[str, Any], context: Any) -> dict[str, Any]:
    """Fail closed: never compile a deliverable that wasn't lead-approved
    or that failed QA. result carries the recovered deliverable context
    (assembled in the agent body below)."""
    ctx = result if isinstance(result, dict) else {}
    reasons: list[str] = []

    # FLOOR 1 — lead approval. Anything other than an explicit approve
    # (needs_rework / hold_for_review / escalate / silence) blocks.
    verdict = ctx.get("lead_verdict", "")
    if verdict != APPROVE_MESSAGE:
        reasons.append(f"not lead-approved (verdict='{verdict or '∅'}')")

    # FLOOR 2 — QA must have passed. A defect (uncited claim, bad citation,
    # contradiction, PII) blocks the compile even if the lead clicked
    # approve. qa_approved == False is an explicit fail.
    if ctx.get("qa_approved") is False:
        reasons.append("QA did not approve the draft")
    defects = {k: ctx.get(k) for k in _QA_DEFECT_KEYS if ctx.get(k)}
    if defects:
        reasons.append("QA defects present: " + ", ".join(sorted(defects)))

    # FLOOR 3 — there must be something to compile.
    if not _section_lines(ctx.get("sections")):
        reasons.append("no deliverable sections to compile")

    if reasons:
        return _blocked(reasons, ctx)

    return compile_deliverable(ctx)


@pre_compose(context_processor=recover_approved_deliverable)
@chain(gate_and_render)
async def deliverable_assembler_agent(
    llm_response: Any,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the recovered deliverable context + the lead's verdict into
    one dict; gate_and_render (@chain) enforces the fail-closed floor and
    does the real render."""
    prepared: dict[str, Any] = {}
    if isinstance(context, dict):
        pd = context.get("prepared_data")
        if isinstance(pd, dict):
            prepared = pd.get("context", pd) if isinstance(pd.get("context"), dict) else pd
    # Direct calls (smoke tests) may pass the deliverable inline with no
    # pre_compose having run — recover it here as a fallback.
    if not prepared:
        prepared = recover_approved_deliverable(input_data, context)

    out = dict(prepared)
    out["lead_verdict"] = _human_message(input_data, context)
    return out
