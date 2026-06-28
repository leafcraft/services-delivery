# services-delivery — example scenarios

Four realistic scenarios that exercise the major paths through this mesh.

## How to run

```bash
python examples/run_examples.py                 # all scenarios
python examples/run_examples.py 01_             # one scenario (substring match)
```

## Scenario map

- **01_new_engagement_memo** — `new_engagement` — Client engagement brief — short strategy memo on market entry. Routine path through brief_intake → research → context → consultant (analysis+draft) → QA.
- **02_friday_status_pack** — `draft_client_status` — Friday end-of-week status report for a multi-stream engagement — engagement_status_drafter pulls the week's activity, drafts the partner-facing pack.
- **03_high_stakes_deliverable** — `manual_action` — Board-level deliverable — engagement-lead-initiated, straight to consultant_agent (analyse + draft). citation_agent confirms factuality, QA tone-checks for the board audience, and QA's tier wires route the editing pass (strategic → senior / SME).
- **04_monthly_steering_pack** — `monthly_steering_pack` — Monthly steering committee pack — exec audience, risk highlights, budget vs actual, decisions sought.

## Audit trail

Every scenario appends one hash-chained entry to the template's
`logs/<audit>.jsonl` file. Tail it during a run to see the
ledger build up across scenarios.
