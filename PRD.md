# services-delivery — Services Delivery — PRD

## Story

Tuesday, 9:40 a.m. **Priya**, an engagement lead at a strategy boutique,
forwards a one-line client ask into the mesh: *"Acme wants a board-ready
memo on whether to enter the EU market — need it Friday."* By Thursday
EOD a cited, partner-defensible memo is on Acme's board deck. Priya spent
40 minutes on it (review + one paragraph), not a day and a half.

The mesh does the routine middle — normalise the brief, pull prior art
and client history in parallel, reason and draft with Claude, fact-check,
verify every citation — and hands humans only the judgment: the SME
sign-off, the senior's edit, and Priya's mandatory client-facing review.
The client is never a mesh member; Priya owns all client contact.

## ASCII flow

```
new_engagement / engagement_status ─► engagement_coordinator_agent (llm, case owner)
                       │  (opens case → brief intake; relays edits → lead; escalates → partner)
                       ▼
                 brief_intake_agent (llm)
                       │
        ┌──────────────┼──────────────────────────┐
        │ (always)     │             (if requires_research)
        ▼              │                            ▼
   context_agent ◄─────┼──── peer bounce (1/session) ──► research_agent
   (llm, loop guard)   │                            (llm, industry_reports KB)
        │              │                                 │
client_question        └──────────── fan-in (wait_for) ──┘
        │                            │
deliverable_due ──────────────►  consultant_agent (Claude + thinking)
manual_action  ──────────────►       analyse AND draft, [cite:] tags
                                     │
                       ┌─────────────┴──────────────┐
                       ▼ (low conf / no evidence)    ▼ (clean)
                   sme_human                      qa_agent (llm)
                                                     │
        ┌──────────────┬─────────────┬──────────────┼─────────────┐
        ▼              ▼             ▼               ▼             ▼
  citation_agent  engagement_   partner_human   [tier matrix]  (PII → partner)
  (programmatic)  lead_human    (if PII)         sme/senior/associate_human
        │         (ALWAYS)          │                  │
        │              │            │      (edited draft) │
        │              │            │                  ▼
        │              │            │      engagement_coordinator_agent
        │              │            │            (relay → lead; no human→human)
        │              ▼            │                  │
        │   approved_for_client ─► deliverable_assembler_agent (FINISHER)
        │              │            │   re-check approve (allow/deny, fail-closed)
        │              │            │   + QA → render ./out/*.md|pdf | delete-on-block
        └──────────────┴────────────┴──────────────────┘
                       ▼  (every terminal path)
              delivery_audit_logger_agent  (hash-chained WORM)

log_time_entry / scope_creep_check ─► timesheet_connector_agent (n8n/PSA)
                                       └─► engagement_controller_agent (SOW drift)
                                            ├─► engagement_lead_human (action)
                                            └─► delivery_audit_logger_agent (clean)

weekly_sync (cron, tz) ─► status_sweep_orchestrator_agent (instances:3, atomic claim)
                          ├─► engagement_lead_human (blocked/overdue)
                          └─► delivery_audit_logger_agent (clean)
draft_client_status / daily_standup_brief / monthly_steering_pack
                   ─► engagement_status_drafter_agent ─► engagement_lead_human
draft_consultant_review ─► performance_review_agent ─► partner / lead
engagement_close_knowledge_capture ─► doc_extraction_agent ─► sme_human
escalation_received ─► partner_human (direct)
```

## Actor inventory (job titles)

| Agent | Job title | Type |
|---|---|---|
| `engagement_coordinator_agent` | Engagement coordinator (case owner) | llm (POD coordinator; opens/status/relays/escalates) |
| `brief_intake_agent` | Intake coordinator | llm |
| `research_agent` | Research analyst | llm **Super-Agent** (dev-store tools + playbook; KB: industry_reports) |
| `context_agent` | Client account researcher | llm (KB: client_context) + loop-guard micro |
| `consultant_agent` | Engagement consultant | llm (Claude + thinking; **house-style skills** + playbook; KB: methodology) |
| `qa_agent` | Quality reviewer | llm + pre-gate stash micro |
| `citation_agent` | Citations checker | programmatic (Python control) |
| `deliverable_assembler_agent` | Deliverable assembler (finisher) | programmatic (fail-closed render to ./out) |
| `engagement_controller_agent` | Engagement controller | llm (KB: engagement_sow_library) |
| `timesheet_connector_agent` | Timesheet/PSA liaison | programmatic (n8n) |
| `status_sweep_orchestrator_agent` | Delivery PMO coordinator | llm (`instances:3` + atomic claim; live cron+tz) |
| `engagement_status_drafter_agent` | Client communications drafter | llm (cron; KB: client_context) |
| `performance_review_agent` | People-ops review drafter | llm (KB: consultant_review_rubric) |
| `doc_extraction_agent` | Knowledge manager | llm (KB: kb_taxonomy) |
| `associate_human` | Associate / IC (tier 1) | human |
| `senior_consultant_human` | Senior consultant (tier 2) | human |
| `sme_human` | Subject-matter expert | human |
| `engagement_lead_human` | Engagement lead / PM | human |
| `partner_human` | Partner | human |
| `delivery_audit_logger_agent` | Compliance / audit recorder | external (custom WORM) |

## Data artifacts

- **EngagementBrief** — `engagement_id`, `client_id`, `practice_area`,
  `deliverable_type`, `scope_summary`, `deadline_utc`,
  `strategic_account`, `requires_research`, `received_at_utc`,
  `raw_payload`. Required: engagement_id, client_id, deliverable_type.
- **Deliverable** — `deliverable_id`, `sections`, `citations`,
  `open_questions`, `confidence`, `uncited_claims`. Required:
  deliverable_id, sections.
- **Audit entry** — `audit_event_id`, `written_at_utc`, `session_id`,
  `payload`, `hash_chain_prev`, `hash_chain_current` (SHA-256 chain).
- **Session stash** — in-process, per session_id: the deliverable
  context (engagement_id, deliverable_id, sections, tier fields…) plus
  the `context_research_bounces` counter.

## Per-agent spec (highlights)

- **brief_intake_agent** — yields the 9 EngagementBrief fields. Policy
  floor in the prompt: strategic_account ⇒ requires_research. Calls
  context (always) + research (if required).
- **research_agent ↔ context_agent** — peers. `context_agent` may bounce
  to `research_agent` when `confidence < 0.6`, bounded to one bounce per
  session by the Python loop guard (`context_retry_exhausted`).
- **consultant_agent** — Claude Sonnet, `thinking_budget: 8192`. One
  role: analyse + draft. **Self-reliant**: `@pre_compose`
  (`load_engagement_context`) pulls its OWN brief + research corpus +
  client account + prior deliverables from the dev store (PROD:
  CRM/KB/archive) so it reasons over real material — not spoon-fed.
  `@compose` shapes per-downstream payloads: the SME gets the analysis +
  the reason review is needed, QA gets the full draft + tier-routing
  fields. `wait_for: "context_agent AND research_agent?"`. Floors (in the
  module, not the prompt): caps confidence at 0.5 and forces
  `requires_sme_review` on an evidence-free hypothesis.
- **qa_agent** — `@pre_compose` (`pull_engagement_record`) pulls the
  engagement record + the resolvable corpus source-ids. The `@chain`
  floor (`qa_floor`) enforces three things the LLM CANNOT waive: (1) the
  **QA gate** — `approved=false` on any uncited/bad-cite/contradiction;
  (2) the **PII floor** — a deterministic regex scan (email/phone/SSN)
  merged into `pii_concerns`, forcing partner review; (3) **stakes→tier
  derivation** — `review_tier` computed in Python. The visible can_call
  wires route on the same fields (legible AND authoritative). Also
  stashes pre-gate context for post-HITL recovery.
- **citation_agent** — deterministic check that each in-text `[cite:]`
  tag is declared AND resolves to a *retrievable* source (the seeded
  corpus in dev, your KB/RAG in prod); coverage floor 0.8. Stays Python
  because "the citations look fine" is the exact failure mode it catches.
- **engagement_status_drafter_agent** — `@pre_compose`
  (`load_engagement_record`) pulls its own engagement record so the
  status report has real content day-0; module floor caps timeline
  confidence at 0.3 on an empty week.
- **engagement_controller_agent** — judgment over PSA-supplied hours;
  never recomputes them (PSA is system of record); computes severity and
  `requires_lead_action`.

## Entry points

`new_engagement`, `client_question`, `weekly_sync` (cron),
`deliverable_due`, `escalation_received`, `knowledge_update`,
`manual_action`, `draft_client_status`, `daily_standup_brief`,
`log_time_entry`, `scope_creep_check`, `monthly_steering_pack`,
`draft_consultant_review`, `engagement_close_knowledge_capture`, plus
direct-call entries (`direct_call_consultant`, `direct_call_qa`,
`direct_call_citation`, `direct_call_engagement_controller`) for
ad-hoc/debug invocation.

## Controls & trust

- **Never auto-deliver** — engagement_lead_human always reviews
  (`condition: "true"`); default-deny `hold_for_review` on timeout.
- **PII → partner** — `pii_concerns != []` routes to partner_human; the
  sme/senior/associate wires all carry `pii_concerns == []`.
- **Citation-or-nothing** — uncited claims auto-reject at QA; coverage
  floor 80% in citation_agent.
- **Confidence gates** — `< 0.7` ⇒ SME; evidence-free hypothesis ⇒ SME.
- **Bounded peer loop** — research↔context capped at one bounce/session.
- **WORM audit** — every terminal path lands in the hash-chained sink.
- **Finisher, fail-closed** — `deliverable_assembler_agent` re-checks lead
  approval by allow/deny token (fail-closed on unknown) + QA, renders the
  real document, deletes any partial on a block.
- **No human→human wire** — humans are first-class but never call each
  other; the IC tiers hand back to `engagement_coordinator_agent` (dual→chain),
  which routes on (chain→dual); `dual` lives only on the human boundary.
- **HITL default-deny** — every gate `fallback_on_timeout` → `held_for_review`
  → the WORM sink; the Manager supervisor carries human→human escalation.

## Config pointer

The department is `configs/config.yaml`. State of record lives in your
systems: hours in the PSA/timesheet system behind
`timesheet_connector_agent` (n8n; cron commented until wired). The weekly
sweep (`status_sweep_orchestrator_agent`) runs day-0 on its live cron —
`instances:3` copies each atomically claim one engagement from the seed
backlog via `agency/tools.py`; wiring tier 3 swaps the claim tool's body
for your PSA query. The full chain runs day-0 on Tier 1 alone against a
seeded dev store (`agency/_shared/store.py`) — a brief, research corpus,
account context and prior deliverables for one realistic engagement; in
prod each store call becomes a connector call (the signatures are the
contract). Agents that PULL in-turn do so through day-0 dev-store tools
(`agency/tools.py`), each announcing `available:false` when it has no
real source.
The Python modules carry the depth — `citation_agent` (control),
`context_agent` (loop guard), `consultant_agent` (`@pre_compose` +
`@compose` + floors), `qa_agent` (`@pre_compose` + `@chain` floors),
`engagement_status_drafter_agent` (`@pre_compose`), and the
`deliverable_assembler_agent` **finisher** (`@pre_compose` recovery +
`@chain` fail-closed render) — each a thing YAML can't express; every
other floor is a prompt contract + `enforce_yields`.

## Reflection / decisions

- **analysis + draft merged into `consultant_agent`** (2.3.x) — one human
  consultant analyses and drafts in one pass; the former internal
  analysis→draft seam survives as the `requires_sme_review == false` gate
  on the QA edge.
- **delivery_router_agent dissolved** — its stakes→tier routing matrix
  now lives as VISIBLE mutually-exclusive conditions on `qa_agent`'s
  outbound wires, so the routing is legible in the config, not hidden in
  a module.
- **time ledger removed from the template** — billable hours belong in
  the firm's PSA; `engagement_controller_agent` is the judgment on top of
  PSA numbers, never the ledger.
- **Slack delivery** — the escalation `channel` target uses
  `channel_id` + `message_template` only; `SLACK_BOT_TOKEN` /
  `SLACK_SIGNING_SECRET` are ChannelConfig fields and need a `channels:`
  block to actually send (documented in `env`, not yet wired).
