"""Dev-mode store for the services-delivery engagement record.

Stands in for the firm's systems of record — the CRM / engagement
database (briefs, client/account context, the engagement record), the
research/knowledge corpus (prior art, industry reports, methodology) and
the deliverables archive (prior deliverables for an account) — so the
FULL chain (intake → research+context → consultant → QA → human review)
runs end-to-end BEFORE any connector is wired.

Self-reliant agents pull their OWN context from here via `@pre_compose`
(the brief, the account context, the research corpus, prior deliverables,
the engagement record) instead of being spoon-fed by a conductor. In
production each read/write becomes the matching connector call — Salesforce/
HubSpot for accounts, your KB/RAG for the corpus, the deliverables archive
for prior IP, your PSA for the engagement record — and the function
signatures here are the contract. Setting the connector env vars alone
does nothing until you wire the calls below.

Single-process JSON files under ./data/. Swap for Redis/Postgres for a
multi-replica deployment; the get/put interface stays the same.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_DIR = Path("./data")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _path(kind: str) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    return _DIR / f"{kind}.json"


def _load(kind: str) -> dict[str, Any]:
    p = _path(kind)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save(kind: str, data: dict[str, Any]) -> None:
    p = _path(kind)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


# ═══════════════════════════════════════════════════════════════════════
# SEED — a single realistic engagement so the day-0 chain has real context
# to reason over. Acme (a strategic account) wants a board-ready memo on
# EU market entry. PROD: this is your CRM + KB + deliverables archive.
# ═══════════════════════════════════════════════════════════════════════
ACME = "cli-acme-industries"
ACME_ENGAGEMENT = "eng-acme-eu-entry-q2"

_SEED_BRIEFS = {
    ACME_ENGAGEMENT: {
        "engagement_id": ACME_ENGAGEMENT,
        "client_id": ACME,
        "client_name": "Acme Industries",
        "practice_area": "strategy",
        "deliverable_type": "memo",
        "scope_summary": "Board-ready memo on whether Acme should enter the EU "
                         "B2B logistics market — regulatory landscape, top-3 "
                         "competitive positioning, go/no-go recommendation.",
        "deadline_utc": "2026-06-26T17:00:00Z",
        "strategic_account": True,
        "requires_research": True,
        "received_at_utc": "2026-06-23T13:40:00Z",
    },
}

# The research corpus an analyst would pull prior art from. PROD: KB/RAG.
_SEED_CORPUS = {
    "rep-eu-logistics-2025": {
        "source_id": "rep-eu-logistics-2025", "kind": "industry_report",
        "topic": "EU B2B logistics market sizing",
        "summary": "EU B2B logistics TAM EUR 1.1T in 2025, 4.2% CAGR; last-mile "
                   "and cross-border customs the two highest-margin segments.",
        "practice_area": "strategy", "published": "2025-11"},
    "rep-eu-customs-reg": {
        "source_id": "rep-eu-customs-reg", "kind": "regulatory_brief",
        "topic": "EU customs & VAT for non-EU operators",
        "summary": "Post-2021 IOSS regime; non-EU operators need an EU fiscal "
                   "representative and an EORI number before bonded warehousing.",
        "practice_area": "regulated", "published": "2025-08"},
    "pp-northwind-eu": {
        "source_id": "pp-northwind-eu", "kind": "past_project",
        "topic": "Northwind EU entry (analogous)",
        "summary": "Northwind entered the EU via a Rotterdam 3PL partnership "
                   "rather than greenfield; reached break-even in 14 months.",
        "practice_area": "strategy", "engagement_id": "eng-northwind-2024"},
    "meth-market-entry": {
        "source_id": "meth-market-entry", "kind": "methodology",
        "topic": "Market-entry decision framework",
        "summary": "Score entry modes on speed, capital, control and "
                   "regulatory exposure; partnership beats greenfield when "
                   "regulatory exposure is high and speed matters.",
        "practice_area": "strategy"},
}

# Client-specific account context. PROD: your CRM (Salesforce/HubSpot).
_SEED_ACCOUNTS = {
    ACME: {
        "client_id": ACME, "client_name": "Acme Industries",
        "tier": "strategic",
        "prior_engagements": [
            {"engagement_id": "eng-acme-supply-2024",
             "title": "Supply-chain resilience review", "year": 2024,
             "outcome": "Implemented; csat 4.6"},
        ],
        "stakeholders": [
            {"name": "R. Okafor", "role": "COO", "disposition": "sponsor"},
            {"name": "L. Méndez", "role": "VP Strategy", "disposition": "skeptic"},
        ],
        "open_threads": [
            "Board wants a go/no-go by end of Q2; COO leans go, VP Strategy "
            "wants the regulatory risk quantified first.",
        ],
        "sensitivities": [
            "Do NOT name the incumbent competitor in writing — legal flagged "
            "it last quarter.",
        ],
    },
}

# Prior deliverables for the account — the consultant reuses framing/IP.
# PROD: the deliverables archive.
_SEED_DELIVERABLES = {
    "del-acme-supply-2024": {
        "deliverable_id": "del-acme-supply-2024", "client_id": ACME,
        "engagement_id": "eng-acme-supply-2024", "deliverable_type": "memo",
        "title": "Supply-chain resilience — board memo",
        "reusable_framing": "Acme's board responds to a clear go/no-go with a "
                            "quantified downside, not an options menu.",
    },
}


def _seed_kind(kind: str, seed: dict[str, Any]) -> dict[str, Any]:
    s = _load(kind)
    if not s:
        s = dict(seed)
        _save(kind, s)
    return s


# ── Briefs / engagement record ───────────────────────────────────────
def get_brief(engagement_id: str) -> dict[str, Any]:
    return _seed_kind("briefs", _SEED_BRIEFS).get(engagement_id, {})


def put_brief(brief: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        s = _seed_kind("briefs", _SEED_BRIEFS)
        eid = brief.get("engagement_id") or new_id("eng")
        brief["engagement_id"] = eid
        s[eid] = {**s.get(eid, {}), **brief}
        _save("briefs", s)
        return s[eid]


def default_engagement_id() -> str:
    """The seeded engagement — the day-0 demo entry point with no inputs."""
    return ACME_ENGAGEMENT


# ── Research corpus ──────────────────────────────────────────────────
def research_corpus(practice_area: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """Prior art the research analyst pulls. PROD: KB/RAG semantic search.
    Dev: the seeded corpus, optionally biased to the practice area."""
    rows = list(_seed_kind("corpus", _SEED_CORPUS).values())
    pa = (practice_area or "").strip().lower()
    if pa:
        rows.sort(key=lambda r: 0 if str(r.get("practice_area", "")).lower() == pa else 1)
    return rows[:limit]


def corpus_source_ids() -> set[str]:
    """Every retrievable source_id — citation_agent's resolvable set in dev."""
    return set(_seed_kind("corpus", _SEED_CORPUS).keys())


def get_source(source_id: str) -> dict[str, Any]:
    """One corpus source by id — the assembler labels cited sources with it."""
    return _seed_kind("corpus", _SEED_CORPUS).get(source_id, {})


# ── Account / client context ─────────────────────────────────────────
def account_context(client_id: str) -> dict[str, Any]:
    return _seed_kind("accounts", _SEED_ACCOUNTS).get(client_id, {})


# ── Prior deliverables (the archive) ─────────────────────────────────
def prior_deliverables(client_id: str) -> list[dict[str, Any]]:
    return [d for d in _seed_kind("deliverables", _SEED_DELIVERABLES).values()
            if d.get("client_id") == client_id]


def put_deliverable(deliverable: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        s = _seed_kind("deliverables", _SEED_DELIVERABLES)
        did = deliverable.get("deliverable_id") or new_id("del")
        deliverable["deliverable_id"] = did
        deliverable["updated_at"] = _now()
        s[did] = {**s.get(did, {}), **deliverable}
        _save("deliverables", s)
        return s[did]


# ═══════════════════════════════════════════════════════════════════════
# ACTIVE-ENGAGEMENT BACKLOG — the weekly status sweep fans out over this.
# The parallel `status_sweep_orchestrator_agent` instances each CLAIM one
# row atomically so the copies split the backlog instead of duplicating it.
# PROD: this list is a query against your PSA / project tracker (Kantata,
# Asana, Monday, Jira); the claim is a `SELECT … FOR UPDATE SKIP LOCKED`
# (or Redis LPOP). The dev store below stands in day-0.
# ═══════════════════════════════════════════════════════════════════════
_SEED_ACTIVE_ENGAGEMENTS = [
    {"engagement_id": ACME_ENGAGEMENT, "client_name": "Acme Industries",
     "stage": "analysis", "deadline_utc": "2026-06-26T17:00:00Z",
     "blocker": "", "days_overdue": 0},
    {"engagement_id": "eng-globex-cost-model", "client_name": "Globex",
     "stage": "research", "deadline_utc": "2026-07-03T17:00:00Z",
     "blocker": "awaiting client cost data", "days_overdue": 0},
    {"engagement_id": "eng-initech-ops-review", "client_name": "Initech",
     "stage": "qa", "deadline_utc": "2026-05-30T17:00:00Z",
     "blocker": "", "days_overdue": 6},
]


def _active_backlog() -> list[dict[str, Any]]:
    """The active-engagement backlog (seeded once, then persisted)."""
    s = _load("active_engagements")
    if not s:
        s = {"queue": [dict(r) for r in _SEED_ACTIVE_ENGAGEMENTS], "claimed": {}}
        _save("active_engagements", s)
    return s  # type: ignore[return-value]


def claim_next_active_engagement(worker_id: str = "") -> dict[str, Any]:
    """Atomically claim ONE unclaimed active engagement for a sweep worker.

    Returns {available: True, ...engagement...} for the claimed row, or
    {available: False, reason: ...} when the backlog is exhausted — NEVER a
    synthetic engagement. The claim is atomic under the process lock (PROD:
    SELECT … FOR UPDATE SKIP LOCKED / Redis LPOP), so parallel copies split
    the backlog instead of duplicating work.
    """
    with _LOCK:
        s = _active_backlog()
        queue = s.get("queue") or []
        if not queue:
            return {"available": False,
                    "reason": "active-engagement backlog is empty — nothing to claim"}
        row = queue.pop(0)
        s["claimed"][row["engagement_id"]] = {
            "worker_id": str(worker_id or ""), "claimed_at": _now()}
        _save("active_engagements", s)
        return {"available": True, **row}


# ── Research corpus search (dev-store fallback for the KB/RAG connector) ──
def search_prior_art(engagement_id: str = "", practice_area: str = "") -> dict[str, Any]:
    """Return prior-art sources for an engagement's practice area.

    Announces itself: {available: False, reason} when there is genuinely
    nothing to return, rather than an empty-but-confident hit. PROD: your
    KB/RAG semantic search.
    """
    pa = (practice_area or "").strip().lower()
    if not pa and engagement_id:
        pa = str(get_brief(engagement_id).get("practice_area", "")).lower()
    rows = research_corpus(pa)
    if not rows:
        return {"available": False,
                "reason": f"no prior art for practice_area={pa or '-'!r}; wire KB_MCP_URL"}
    return {"available": True, "practice_area": pa, "sources": rows,
            "resolvable_source_ids": sorted(corpus_source_ids())}


def list_resolvable_sources() -> dict[str, Any]:
    """Every retrievable source_id — the citation-or-nothing resolvable set."""
    ids = sorted(corpus_source_ids())
    if not ids:
        return {"available": False, "reason": "no corpus loaded; wire KB_MCP_URL"}
    return {"available": True, "resolvable_source_ids": ids}
