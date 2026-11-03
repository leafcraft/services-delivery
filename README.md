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
record. Nothing is spoon-fed by a conductor. Agents that PULL in-turn do
so through day-0 dev-store **tools** (`agency/tools.py`) — `search_prior_art`
/ `list_resolvable_sources` for research, `claim_next_active_engagement`
for the parallel sweep — and every tool **announces itself**
(`available: false` with a reason) when it has no real source, never a
synthetic empty-but-confident hit.

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
| `OPENAI_API_KEY` | `gpt-4o-mini` agents (coordinator, intake, research, context, qa, controller, status-sweep, doc-extraction, manager) |
| `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` agents (consultant, status drafter, performance review) |

### Tier 2 — host prerequisites & adapters (recommended)
| Var | Purpose |
|---|---|
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | Engagement context, deliverable history, session store (30-day TTL). Defaults to `localhost:6379` |
| *(vault connection `escalation_webhook`)* | Escalation webhook target (e.g. PagerDuty): create vault connection `escalation_webhook` (an "Add custom" record on the Infrastructure page, service type 'Outbound webhook'), then uncomment the webhook target (in `configs/config.yaml` before the first start, in Studio's YAML editor after it — see *Changing the configuration after the first start*) |
| `LEAFMESH_ENV_TOKEN` | Host/SDK environment token |

### Tier 3 — connectors (optional; light up external systems)
| Var | Purpose |
|---|---|
| vault connection `psa_timesheet_n8n` | n8n workflow fronting your PSA/timesheet system (Harvest, Toggl, ClickTime, Tempo, Kantata) — hours + budget position. Create it on Infrastructure → Integrations → n8n (base URL = the workflow's webhook URL), then uncomment `connection: psa_timesheet_n8n` on `timesheet_connector_agent` |
| vault connection `kb_mcp` | Your KB / RAG MCP server for `research_agent`; create it on Infrastructure → Integrations → MCP, then uncomment the `mcp:` block (in `configs/config.yaml` before the first start, in Studio's YAML editor after it). The day-0 research tools count the KB as wired as soon as this record exists. |
| `SLACK_DELIVERY_CHANNEL` | `channel_id` for the escalation `channel` target, which ships **commented** (day 0 the partner gets every escalation in the Inbox). To turn it on: connect Slack on Infrastructure → Messaging → Slack (the bot token and signing secret live there, never in `.env`), give `partner_human` `channels: {slack: {connection: <name>}}`, set this id, then uncomment the target |

The `status_sweep_orchestrator_agent` sweep runs **day-0** on its live
Monday cron against the seed backlog (via the `claim_next_active_engagement`
dev-store tool); wiring tier 3 only swaps that tool's body for your PSA
query. (2.4.484's `scaling.copies.mode: auto` with `claim_tool:
claim_next_active_engagement` could replace this manual split — an owner
decision, not made in this template.) After wiring tier 3, un-comment the `# wake_up:` cron on
`timesheet_connector_agent`, and uncomment the `# mcp:` block on
`research_agent` once you create the `kb_mcp` vault connection — in
`configs/config.yaml` before the first start, in Studio's YAML editor (or
`PUT /api/yaml/raw`) after it; it applies live, no restart.

> **HITL delivery:** the human members answer in Studio's Inbox. To notify an
> external tool instead, create a vault connection (an "Add custom" record
> on the Infrastructure page, service type 'Outbound webhook') and set
> `webhook_config: {connection: <name>}` on the seat.

## Agents

Every agent is a real role a consulting firm would recognise. The mesh
boundary is sacred — the **client is never a member**; the engagement
lead owns all client contact.

| Agent | Role | Responsibilities | Can perform |
|---|---|---|---|
| `engagement_coordinator_agent` | **Engagement coordinator (case owner)** | The POD's single point of contact — opens the case, keeps status, **relays** an IC-edited draft to the lead, escalates. Never does specialist work. Keeps humans first-class with **no human→human wire** (IC tiers hand back here; it routes on) | → `brief_intake_agent` (open), → `engagement_lead_human` (route to review), → `partner_human` (escalate), → `delivery_audit_logger_agent` |
| `brief_intake_agent` | Intake coordinator | Normalises any inbound ask (email, Slack, SOW, meeting note) into a canonical `EngagementBrief`; enforces the strategic-account → research policy floor | → `context_agent` (always), → `research_agent` (if research required), → `engagement_lead_human` (unworkable brief) |
| `research_agent` | Research analyst (**Super-Agent**) | Wrapped in the Super-Agent orchestrator (plan → pull → verify → synthesise); pulls prior art via day-0 dev-store tools (`search_prior_art`) + the `industry_reports` KB; a `prior_art_search` / `gap_flagging` **playbook** carries the craft; citation-or-nothing; flags `knowledge_gaps` | ↔ `context_agent` (peer cross-reference, chain); fans in to consultant |
| `context_agent` | Client account researcher | Pulls client-specific history, stakeholder map, open threads, sensitivities from the `client_context` KB; loop-guarded peer bounce | ↔ `research_agent` (one bounce/session, chain) |
| `consultant_agent` | Engagement consultant | `@pre_compose` pulls its own brief + research corpus + account context + prior deliverables; reasons (Claude + 8 k thinking) **and** writes the first-pass deliverable with `[cite:]` tags; loads the firm **house-style skills** + a `build_memo` / `build_recommendation` playbook; `@compose` shapes SME vs QA payloads; caps evidence-free confidence | → `sme_human` (low conf / no-evidence), → `qa_agent` (clean) |
| `qa_agent` | Quality reviewer | `@pre_compose` pulls the engagement record; `@chain` `qa_floor` enforces the QA gate (uncited/bad-cite/contradiction → reject), a deterministic PII scan, and stakes→tier derivation — none waivable; visible tier wires route on the derived fields | → `citation_agent`, → `engagement_lead_human` (always), → `partner_human` (PII), → `sme_human` / `senior_consultant_human` / `associate_human` (tier) |
| `citation_agent` | Citations checker | Deterministic verification that every `[cite:]` resolves to a *retrievable* source (the corpus in dev, your KB/RAG in prod); coverage floor 80% (Python control, not LLM judgment) | → `engagement_lead_human` (gaps), → `delivery_audit_logger_agent` (clean) |
| `engagement_controller_agent` | Engagement controller | SOW-drift judgment over PSA-supplied hours; surfaces scope creep and change-order candidates; never recomputes the PSA's numbers | → `engagement_lead_human` (action needed), → `delivery_audit_logger_agent` (clean) |
| `timesheet_connector_agent` | Timesheet/PSA liaison | Forwards time entries to and pulls hours + budget position from your PSA/timesheet system (n8n connector) | → `engagement_controller_agent` (when PSA resolves the engagement) |
| `status_sweep_orchestrator_agent` | Delivery PMO coordinator | Weekly status sweep — **3 parallel copies (`scaling.copies.count: 3`)** each **atomically claim** one active engagement from the backlog (`claim_next_active_engagement`) so they split the work, then combine once; live Monday cron with a `timezone` | → `engagement_lead_human` (blocked/overdue), → `delivery_audit_logger_agent` (clean) |
| `engagement_status_drafter_agent` | Client communications drafter | `@pre_compose` pulls its own engagement record; drafts the weekly Friday status report / daily standup brief / monthly steering pack; caps confidence on an empty week | → `engagement_lead_human` (review before send — never auto-sent) |
| `performance_review_agent` | People-ops review drafter | Drafts consultant Q/annual reviews weighting client csat + repeat engagement + outcomes over utilisation; HR system signs | → `partner_human` (principal-track), → `engagement_lead_human` (PIP/exit/routine) |
| `doc_extraction_agent` | Knowledge manager | At engagement close, mines deliverables for reusable IP, anonymises the client, tags reusability, skips client-specific content | → `sme_human` (high-reusability review) |
| `associate_human` | Associate / IC (tier 1) | Editing + personalisation pass on routine deliverables (Inbox, 4 h) | → `research_agent` (more research), → `engagement_coordinator_agent` (edited / escalate), → `delivery_audit_logger_agent` (anything else) |
| `senior_consultant_human` | Senior consultant (tier 2) | Editing pass on strategic accounts and recommendation deliverables (Inbox, 6 h) | → `engagement_coordinator_agent` (edited / escalate), → `delivery_audit_logger_agent` (anything else) |
| `sme_human` | Subject-matter expert | Depth review on regulated / tax / healthcare / security / legal areas and low-confidence drafts (Inbox, 8 h) | → `engagement_coordinator_agent` (edited / escalate), → `delivery_audit_logger_agent` (anything else) |
| `engagement_lead_human` | Engagement lead / PM | Owns the relationship; **always** reviews before the client sees any output (Inbox, 6 h) | → `deliverable_assembler_agent` (approve → finisher), → `engagement_coordinator_agent` (escalate), → `delivery_audit_logger_agent` (rework / anything else) |
| `deliverable_assembler_agent` | Deliverable assembler (**finisher**) | Runs AFTER lead approval; re-checks approval (allow/deny tokens, **fail-closed on unknown**) + QA, renders the real client document to `./out/*.md\|pdf`, **deletes any partial on a block**; day-0 archive write via the dev store | → `delivery_audit_logger_agent` |
| `partner_human` | Partner | Exec sponsor and escalation tier (Inbox, 12 h); the Manager's escalation target | → `delivery_audit_logger_agent` |
| `delivery_audit_logger_agent` | Compliance / audit recorder | Hash-chained WORM audit of every QA outcome, edit, approval, escalation | terminal |

## Entry points (start from anywhere)

`new_engagement` (→ coordinator opens the case), `engagement_status`
(→ coordinator answers from the case record), `client_question`,
`weekly_sync` (cron), `deliverable_due`, `escalation_received`,
`knowledge_update`, `manual_action`, `draft_client_status`,
`daily_standup_brief`, `log_time_entry`, `scope_creep_check`,
`monthly_steering_pack`, `draft_consultant_review`,
`engagement_close_knowledge_capture`, plus direct-call entries for
ad-hoc/debug invocation.

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
- **Finisher, fail-closed** — `deliverable_assembler_agent` re-checks lead
  approval by allow/deny token (fail-closed on an unknown verdict) + QA,
  renders the real document, and **deletes any partial on a block**
- **No human→human wire** — humans are first-class but never call each
  other; IC tiers hand their edited draft back to the **coordinator**
  (dual→chain), which routes it on (chain→dual). No `dual↔dual` ping-pong
- **AI specialists are `chain`** — `dual` lives only on the human/webhook
  boundary agents (gotcha #17)
- **Bounded peer loop** — research↔context capped at one bounce/session
- **Hash-chained WORM** — every QA outcome, edit, approval, escalation
- **Silence never advances an engagement** — if nobody answers a gate in
  time, the request waits no longer: its Inbox card closes and the ticket
  records that no reply came. Nothing reaches the client and nobody is
  chased automatically. Every seat routes every reply: the words its
  prompt asks for, and anything else → the WORM audit. The Manager
  escalates to the partner only when an agent hits an error a retry cannot
  fix, when 3 different agents fail in one session, or when restarts run
  out
- **Fan-in, said plainly** — the consultant waits for context (research
  optional). If research arrives after the consultant has already run, the
  consultant runs again on everything gathered (an update), so a second
  draft can reach QA and the human reviewers for the same deliverable

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

## Changing the configuration after the first start

`configs/config.yaml` is only the first-start seed. On the first start (an empty store) the agency imports it as version 1 and from then on keeps its configuration in the store. After that the file is not read again: an edit to it is **not** applied, even after a restart (the boot log says "that edit was NOT applied").

To change a running agency:

- **Studio** — its settings pages, or the raw YAML editor.
- **API** — `PUT /api/yaml/raw` with an `X-API-Key` header and the body `{"yaml_content": "<the whole YAML>", "save_to_disk": true}`, or `PATCH /api/yaml/config` with `{"updates": {...}, "save_to_disk": true}`.

Each save is a new version and applies live on every pod — no restart. (A few settings, such as brokers, make the agency reload itself in place within seconds.) So wherever this README or a comment says "uncomment X in `configs/config.yaml`", do it in the file before the first start, or in Studio's YAML editor after it.

## Where the data store is set

From leafmesh 2.4.482 the store (Redis or Postgres) is set **only in `.env`** — never in `configs/config.yaml`. A `persistence:` block in the YAML is ignored with a warning on leafmesh 2.4.482–2.4.485; from 2.4.486 the agency refuses to start if that block differs from `.env`, and names each `.env` line to add. The project's `.env` sets the store explicitly (`LEAFMESH_STORE`, `LEAFMESH_AGENCY_NAME`, `REDIS_*`).

- Redis (default): `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`; optional `REDIS_DEFAULT_TTL`, `REDIS_SESSION_TTL`, `REDIS_SSL*`, `REDIS_CLUSTER_NODES`.
- Postgres instead: `LEAFMESH_STORE=postgres` plus `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (optional `POSTGRES_SSLMODE`).
- Or the whole server in one value: `REDIS_URL=redis://user:pw@host:6379/0` (`rediss://` turns TLS on) or `DATABASE_URL=postgresql://user:pw@host:5432/db`. The single names fill in what the URL leaves out.
- With Docker Compose the app reaches Redis as `redis` (the service name); compose sets that for you.
- Check it in Studio → **Infrastructure → Datastores**: it shows whether the store is reachable (never the address or password).

## Your vault key — back it up

`create-leafmesh` put a `LEAFMESH_VAULT_KEY` in your `.env` when it made this project. It is the master key that encrypts every connection and credential you store in the vault (in Redis / Postgres).

- **Back it up like a master password.** Lose it and every stored connection has to be entered again.
- **Never change or regenerate it** for an existing project — a new key locks the existing vault. To change it, use `leafmesh vault rotate-key`.
- Every copy of the app (every replica) must get the **same** value. Docker Compose passes it in from `.env` as an environment variable.
- Never commit `.env` (it is already in `.gitignore`).
