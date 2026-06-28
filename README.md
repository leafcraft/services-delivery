# services-delivery — Services Delivery

A department of self-reliant agents that does the routine ~65% of
professional-services delivery work — brief intake, research, client
context, analysis-and-draft, QA, citation checking — while the engagement
lead, ICs, SMEs and partner stay in the loop. **No deliverable ever
reaches a client without a human reviewing it first.**

## Story

Tuesday, 9:40 a.m. **Priya**, an engagement lead at a strategy boutique,
forwards a one-line client ask into the mesh via `new_engagement`: *"Acme
wants a board-ready memo on whether to enter the EU market — need it
Friday."*

`brief_intake_agent` normalises it into a canonical `EngagementBrief`
(client_id `acme`, practice_area `strategy`, deliverable_type `memo`,
strategic_account `true`). Because Acme is a strategic account, the
policy floor forces `requires_research = true`. The brief fans out in
parallel: `context_agent` pulls Acme's prior engagements and stakeholder
map from the `client_context` knowledge base; `research_agent` pulls EU
market-entry prior art and industry reports, citing each finding by
source. The two are peers — `context_agent`, finding thin support,
bounces once to `research_agent` for more prior art (the loop guard caps
it at a single bounce per session, so they never ping-pong).

The two streams fan in to `consultant_agent` — Claude Sonnet with an
8 k thinking budget — which reasons over the synthesis **and** writes the
first-pass memo, tagging every factual claim with `[cite: source_id]`.
`qa_agent` fact-checks it; `citation_agent` mechanically verifies each
`[cite:]` resolves to a real source (coverage floor 80%). QA finds the
draft clean, no PII, confidence high — so its visible tier matrix routes
the editing pass to `senior_consultant_human` (strategic account,
high confidence, not a regulated area) and, **always**, to
`engagement_lead_human`. Priya reviews, tightens one paragraph, approves
for client. Every step — QA outcome, the senior's edit, Priya's approval
— lands in a hash-chained WORM audit. The memo is on Acme's board deck by
Thursday EOD; Priya spent 40 minutes, not a day and a half.

## Why it works in an organization

Professional services is the sharpest single ROI case in the firm:
billable people are **both** your biggest cost **and** all your revenue,
so one improvement compounds on both sides at once. This mesh hits two
ROI cells with one department:

- **util × services** — more output per billable head (the pool earns
  ~30% of revenue; ~65% of delivery work is routine).
- **hours × services** — fewer hours per deliverable (the pool earns
  ~18% of revenue; same ~65% routine share).

Cost-out and revenue-added land together. The mesh deflects the
mechanical work (intake normalisation, prior-art pulls, first-pass
drafting, citation checking, status reports, timesheet aggregation,
scope-creep detection) and hands humans only the judgment: the SME
sign-off, the senior's edit, the lead's client-facing review. State of
record never lives in the template — hours sit in your PSA/timesheet
system, the engagement sweep in your project tracker.

## Runs on day 0 (no connectors)

The full chain runs end-to-end on **Tier 1 alone** against a seeded dev
store (`agency/_shared/store.py`, JSON under `./data/`). It ships one
realistic engagement — Acme Industries, a strategic account, wants a
board-ready memo on EU market entry — with a brief, a research corpus
(industry report, regulatory brief, an analogous past project,
methodology), the client account context (stakeholders, open threads,
sensitivities) and a prior deliverable. Self-reliant agents pull this
context **themselves** via `@pre_compose` — the consultant pulls the
brief + corpus + account + priors; QA pulls the engagement record it
verifies citations against; the status drafter pulls the engagement
record. Nothing is spoon-fed by a conductor.

Copy-paste demo (after Quick start, with the mesh running you can call an
entry point directly):

```python
import asyncio
from leafmesh import LeafMesh
import agency._shared.audit_logger  # registers the WORM sink

async def demo():
    sdk = LeafMesh.from_yaml("configs/config.yaml")
    await sdk.start()
    # No payload needed — the consultant's @pre_compose falls back to the
    # seeded Acme engagement and reasons over the real corpus.
    result = await sdk.mesh_call("deliverable_due", {}, session_id="demo-1")
    print(result)
    await sdk.stop()

asyncio.run(demo())
```

Or run the bundled scenarios: `python examples/run_examples.py`.

In production, each store read/write becomes the matching connector call
(CRM for accounts, your KB/RAG for the corpus, the deliverables archive,
your PSA for the engagement record); the function signatures in
`store.py` are the contract. Setting the connector env vars alone does
nothing until you wire those calls.

## Configuration (three tiers)

Copy `env` to `.env` and fill in by tier. The mesh BOOTS on tier 1
alone; tiers 2–3 light up host prerequisites and external systems.

### Tier 1 — boot (required)
| Var | Purpose |
|---|---|
| `LEAFMESH_LICENSE_KEY` | Required — the SDK refuses to start without it |
| `OPENAI_API_KEY` | `gpt-4o-mini` agents (intake, research, context, qa, controller, doc-extraction, manager) |
| `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` agents (consultant, status drafter, performance review) |

### Tier 2 — host prerequisites & adapters (recommended)
| Var | Purpose |
|---|---|
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | Engagement context, deliverable history, session store (30-day TTL). Defaults to `localhost:6379` |
| `PAGER_WEBHOOK` | Escalation webhook target (defaults to a PagerDuty Events v2 URL) |
| `LEAFMESH_ENV_TOKEN` | Host/SDK environment token |

### Tier 3 — connectors (optional; light up external systems)
| Var | Purpose |
|---|---|
| `PSA_TIMESHEET_WEBHOOK_URL` / `PSA_TIMESHEET_AUTH_TOKEN` | n8n workflow fronting your PSA/timesheet system (Harvest, Toggl, ClickTime, Tempo, Kantata) — hours + budget position |
| `PSA_STATUS_SWEEP_WEBHOOK_URL` / `PSA_STATUS_SWEEP_AUTH_TOKEN` | n8n workflow fronting your PSA/project tracker (Kantata, Asana, Jira, Monday) — active-engagement sweep |
| `SLACK_DELIVERY_CHANNEL` | `channel_id` the escalation `channel` target posts to |
| `SLACK_BOT_TOKEN` / `SLACK_SIGNING_SECRET` | Fields of a `channels: {slack: {...}}` block (ChannelConfig) — **not** auto-consumed by the escalation channel target. Add a `channels:` block to `configs/config.yaml` to actually deliver Slack messages |

After wiring tier 3, un-comment the `# wake_up:` crons on
`timesheet_connector_agent` and `status_sweep_orchestrator_agent` in
`configs/config.yaml`.

## Agents

Every agent is a real role a consulting firm would recognise. The mesh
boundary is sacred — the **client is never a member**; the engagement
lead owns all client contact.

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `brief_intake_agent` | Intake coordinator | Normalises any inbound ask (email, Slack, SOW, meeting note) into a canonical `EngagementBrief`; enforces the strategic-account → research policy floor | → `context_agent` (always), → `research_agent` (if research required) |
| `research_agent` | Research analyst | Pulls prior art, industry reports, methodology from the `industry_reports` KB; citation-or-nothing; flags `knowledge_gaps` for ingestion | ↔ `context_agent` (peer cross-reference); fans in to consultant |
| `context_agent` | Client account researcher | Pulls client-specific history, stakeholder map, open threads, sensitivities from the `client_context` KB; loop-guarded peer bounce | ↔ `research_agent` (one bounce/session) |
| `consultant_agent` | Engagement consultant | `@pre_compose` pulls its own brief + research corpus + account context + prior deliverables; reasons (Claude + 8 k thinking) **and** writes the first-pass deliverable with `[cite:]` tags; `@compose` shapes SME vs QA payloads; caps evidence-free confidence | → `sme_human` (low conf / no-evidence), → `qa_agent` (clean) |
| `qa_agent` | Quality reviewer | `@pre_compose` pulls the engagement record; `@chain` `qa_floor` enforces the QA gate (uncited/bad-cite/contradiction → reject), a deterministic PII scan, and stakes→tier derivation — none waivable; visible tier wires route on the derived fields | → `citation_agent`, → `engagement_lead_human` (always), → `partner_human` (PII), → `sme_human` / `senior_consultant_human` / `associate_human` (tier) |
| `citation_agent` | Citations checker | Deterministic verification that every `[cite:]` resolves to a *retrievable* source (the corpus in dev, your KB/RAG in prod); coverage floor 80% (Python control, not LLM judgment) | → `engagement_lead_human` (gaps), → `delivery_audit_logger_agent` (clean) |
| `engagement_controller_agent` | Engagement controller | SOW-drift judgment over PSA-supplied hours; surfaces scope creep and change-order candidates; never recomputes the PSA's numbers | → `engagement_lead_human` (action needed), → `delivery_audit_logger_agent` (clean) |
| `timesheet_connector_agent` | Timesheet/PSA liaison | Forwards time entries to and pulls hours + budget position from your PSA/timesheet system (n8n connector) | → `engagement_controller_agent` (when PSA resolves the engagement) |
| `status_sweep_orchestrator_agent` | Delivery PMO coordinator | Weekly status sweep across active engagements from your PSA/project tracker (n8n connector; cron commented until wired) | terminal (n8n posts the report) |
| `engagement_status_drafter_agent` | Client communications drafter | `@pre_compose` pulls its own engagement record; drafts the weekly Friday status report / daily standup brief / monthly steering pack; caps confidence on an empty week | → `engagement_lead_human` (review before send — never auto-sent) |
| `performance_review_agent` | People-ops review drafter | Drafts consultant Q/annual reviews weighting client csat + repeat engagement + outcomes over utilisation; HR system signs | → `partner_human` (principal-track), → `engagement_lead_human` (PIP/exit/routine) |
| `doc_extraction_agent` | Knowledge manager | At engagement close, mines deliverables for reusable IP, anonymises the client, tags reusability, skips client-specific content | → `sme_human` (high-reusability review) |
| `associate_human` | Associate / IC (tier 1) | Editing + personalisation pass on routine deliverables | → `engagement_lead_human`, → `senior_consultant_human` (escalate), → `research_agent` (more research) |
| `senior_consultant_human` | Senior consultant (tier 2) | Editing pass on strategic accounts and recommendation deliverables | → `engagement_lead_human` |
| `sme_human` | Subject-matter expert | Depth review on regulated / tax / healthcare / security / legal areas and low-confidence drafts | → `engagement_lead_human`, → `partner_human` (escalate) |
| `engagement_lead_human` | Engagement lead / PM | Owns the relationship; **always** reviews before the client sees any output; default-deny on timeout | → `delivery_audit_logger_agent`, → `partner_human` (escalate) |
| `partner_human` | Partner | Exec sponsor and escalation tier; default-deny on timeout | → `delivery_audit_logger_agent` |
| `delivery_audit_logger_agent` | Compliance / audit recorder | Hash-chained WORM audit of every QA outcome, edit, approval, escalation | terminal |

## Entry points (start from anywhere)

`new_engagement`, `client_question`, `weekly_sync` (cron),
`deliverable_due`, `escalation_received`, `knowledge_update`,
`manual_action`, `draft_client_status`, `daily_standup_brief`,
`log_time_entry`, `scope_creep_check`, `monthly_steering_pack`,
`draft_consultant_review`, `engagement_close_knowledge_capture`, plus
direct-call entries for ad-hoc/debug invocation.

## Trust mechanisms (deterministic floors the LLM can't waive)

These are enforced in Python `@chain` floors that run AFTER the model —
not asked for in a prompt:

- **QA gate** — `qa_floor` forces `approved = false` whenever there are
  uncited claims, unresolved citations, or contradictions. No exception.
- **PII floor** — `qa_floor` runs a deterministic regex scan (email /
  phone / SSN) over the section bodies and merges hits into
  `pii_concerns` even if the model called the draft clean → forces
  partner review. PII never ships.
- **Stakes→tier derivation** — `qa_floor` computes `review_tier`
  (associate / senior / sme / partner) in Python from the stakes; the
  **visible** can_call wires route on the same fields, so the matrix is
  both legible and authoritative.
- **No-evidence cap** — `consultant_agent` caps confidence at 0.5 and
  forces SME review when a hypothesis has no supporting evidence.
- **Citation resolution** — `citation_agent` verifies each `[cite:]`
  resolves to a *retrievable* source (the corpus in dev; your KB/RAG in
  prod), coverage floor 80% (deterministic, not LLM judgment).
- **Empty-week cap** — `engagement_status_drafter_agent` caps timeline
  confidence at 0.3 when there's no real activity.

And the structural guarantees:

- **Engagement lead always reviews** — no auto-deliver to client, ever
- **Bounded peer loop** — research↔context capped at one bounce/session
- **Hash-chained WORM** — every QA outcome, edit, approval, escalation
- **HITL fallback chain** — associate → senior → lead → partner → halt
  (all default-deny on timeout)

## YAML-first, with hands where roles need them

The mesh is defined in `configs/config.yaml` — a practice lead reading it
sees their department. Python lives only where a role genuinely needs
hands:

- `citation_agent` — deterministic citation resolution (a control, not
  judgment).
- `context_agent` — the research↔context loop guard (per-session state).
- `consultant_agent` — `@pre_compose` pulls its own brief + corpus +
  account + priors; `@compose` gives the SME the analysis and QA the full
  draft; floors cap evidence-free confidence.
- `qa_agent` — `@pre_compose` pulls the engagement record; `@chain`
  `qa_floor` enforces the QA / PII / tier floors above; plus the pre-gate
  stash for post-HITL recovery.
- `engagement_status_drafter_agent` — `@pre_compose` pulls the
  engagement record so the status report has real content day-0.

Every other floor — clamps, ID synthesis, scoring formulas, identity
passthrough on the connector/controller agents — is written into the
agent prompts as explicit contract rules and enforced by `enforce_yields`.

## Quick start

```bash
cp env .env            # fill in tier-1 keys at minimum
pip install -r requirements.txt
docker run -d -p 6379:6379 redis:7-alpine
python validate_config.py
python main.py         # API docs: http://127.0.0.1:18820/docs
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -q
```
