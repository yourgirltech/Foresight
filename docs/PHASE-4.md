# Phase 4 — Patient-access data tools (03 OCR · 04 COB · 05 Cost Estimate)

_Status: **SPEC — awaiting review.** Three agent specs:
[`agents/03-ocr-agent.md`](agents/03-ocr-agent.md),
[`agents/04-cob-agent.md`](agents/04-cob-agent.md),
[`agents/05-cost-estimate-agent.md`](agents/05-cost-estimate-agent.md). No code
yet. Approve, then build one agent at a time in the Phase 1–3 order:
migration → agent → seed → UI → tests._

**Goal.** Close out the "before / at the point of care" stage — the three things
a front desk needs the moment a patient is in front of them, none of which is a
simulation:

| # | Agent | What it does | AI? |
|---|-------|--------------|-----|
| 03 | **ocr-agent** | a real photo of an insurance card → structured member ID / group / payer / plan, each confidence-flagged; a human confirms before anything is saved as authoritative | **yes** — Claude vision, the `ANTHROPIC_API_KEY` already wired for 07 |
| 04 | **cob-agent** | a patient with 2+ active coverages → which is primary / secondary, with the specific COB rule that decided it cited | no — pure deterministic rule engine (NAIC model regulation) |
| 05 | **cost-estimate-agent** | a self-pay patient + planned procedures → a No Surprises Act Good Faith Estimate; the number is computed deterministically, the AI only phrases it in plain language | **only for phrasing** — never computes the number |

## The architectural decision: none of these three is a Commander agent

Phases 1–3 built the **autonomous agent pipeline** — 00-Commander routes a claim /
eligibility check / prior-auth row through numbered specialists (06→07→08→09/10,
E1–E7, A1–A11), advancing a workflow state machine behind a human-approval gate.
That shape earns the Commander: multi-step, event-driven, a lifecycle with
branches and escalations.

**03, 04, and 05 have none of that shape.** Each is a **synchronous
request → response tool a human invokes directly**:

- **03** — one round trip: upload image → one Claude vision call → return
  extracted fields. No state machine, no routing, no follow-on. It is not part of
  the claim / eligibility / auth lifecycle.
- **04** — a pure function over a patient's coverage rows, called while rendering
  an insurance summary. It is `rules.py` (06) in a new domain — deterministic,
  no I/O, no routing — just invoked by an endpoint instead of the orchestrator.
- **05** — a user action ("estimate this visit") → a deterministic price lookup
  → one Claude call for wording → a rendered document. One pass, no lifecycle.

Wrapping any of them in a Commander trigger family and rule block would be
ceremony without benefit — there is no decision for the Commander to make and no
agents to route between. So **Phase 4 adds zero Commander surface.** `commander.py`
after Phase 4 is byte-identical to after Phase 3 (R1–R20, E1–E7, A1–A11); the
existing `commander_test.py` / `eligibility_commander_test.py` /
`prior_auth_commander_test.py` still pass untouched.

The human-in-the-loop principle still holds — it is enforced **at the endpoint /
UI layer** instead of in a rule table:

- **03** never writes an extracted field onto an appointment or patient record
  without an explicit human "confirm" action. A low-confidence or ambiguous
  extraction is a draft for a person to correct, not an autonomous write.
- **04** only *reports* an ordering with its reasoning cited; it changes no
  coverage row and triggers no downstream action. A human reads it and can
  override.
- **05** produces a document for a human to review and hand to the patient; it
  bills nothing and commits nothing.

## Shared conventions (all three)

- Same tenancy rule as every prior phase: every table carries
  `organization_id uuid not null references public.organizations(id)` and is run
  through `select public.enable_tenant_isolation('public.<table>')`. No hardcoded
  tenant id.
- **No `patients` table.** The codebase identifies a patient softly by
  `patient_name` + `patient_dob` within an org (same as `appointments`), or links
  to an `appointment_id` directly. A real `patients` table is a future refactor,
  out of scope for Phase 4.
- Backend-mediated writes only — no new client write grants (Phase 2–3
  discipline). Reads go through the caller's RLS-scoped JWT; writes derive
  `organization_id` from the verified session.
- AI calls (03, 05) reuse 07's pattern exactly: `anthropic.AsyncAnthropic`,
  `client.messages.create`, model from a settings field defaulting to
  `claude-opus-5`, a strict system prompt, graceful `*Unavailable` on API error.

## Test standard (per the review request)

| Agent | Standard |
|-------|----------|
| 03 | **exhaustive** unit tests of the confidence-gating logic (pure function over synthetic field/confidence combinations — the "does this extraction need human review" decision); an **opt-in `--live`** integration test that calls Claude vision with committed *synthetic* card images (clearly fake, no real PII) and asserts structure + that an illegible field returns `null`, never a guess |
| 04 | **exhaustive fuzz** over the COB rule space — same standard as the eligibility / prior-auth fuzz: determinism, exactly one primary, the cited rule actually explains the ordering, plus every textbook case (birthday rule, employee-over-dependent, Medicaid-last, active-over-retiree) |
| 05 | **NSA gating** proven exhaustively: refused when the patient has an active coverage row, refused when `self_pay` is not affirmed, allowed only for an affirmed self-pay patient with no active coverage; deterministic pricing arithmetic; the AI `phrase()` step is opt-in `--live` |

## Delivery order

1. **03 — OCR.** Adds Supabase Storage (a private `card-scans` bucket +
   `python-multipart`). Biggest infra delta.
2. **04 — COB.** Pure logic, no infra. Fastest.
3. **05 — Cost Estimate.** Depends on 04 (reads `patient_coverages` for the
   self-pay gate) — build it last.

## Open items carried forward

Tracked so they are not lost as later phases land. Add to this list rather than
leaving a TODO in code.

| # | Item | State | Notes |
|---|------|-------|-------|
| O-1 | **Schedule the card-image retention purge.** `scripts/purge_expired_card_images.py` + `app.card_retention.purge_expired_images()` delete confirmed-scan images past `image_retain_until` and rejected-scan images (retention 0), stamping `card_scans.image_purged_at`. The job is **built, tested (`tests/card_scan_purge_test.py`), and idempotent**, but it is **run manually** — nothing invokes it on a schedule. This is the codebase's first job that needs a real recurring trigger (there is no scheduler yet; Phase 1–3 are all request/event-driven). Wire it to cron / a worker / Supabase scheduled function when scheduler infra is introduced (a natural Phase 5+ "operational jobs" item). Until then: run it manually during any pilot with real PHI. |
| O-2 | **`ocr.CONFIRM_IMAGE_RETENTION_DAYS = 90` is provisional.** Flagged in-code for compliance review before production — legal/compliance must set the real retention period and sign off (03 §9.1). |
| O-3 | **`NSA_GFE_DISCLAIMER` (05)** must carry a "needs legal sign-off before any real patient sees it" comment — enforce when 05 is built. |

See each agent doc for the full data model, contract, and test plan.
