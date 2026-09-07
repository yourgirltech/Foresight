# Foresight — compliance & legal checklist before real PHI

**Purpose.** Foresight runs on **synthetic data only** today. This is the single
consolidated list of everything that must be resolved — by legal, compliance,
the customer, or engineering **gated on one of their decisions** — before a real
patient record, insurance card, claim, or estimate enters the system, in a
pilot, a customer demo with real data, or production.

This document is written to be handed to outside counsel or a compliance
consultant. Sections 1–4 are the checklist. Section 5 explains what the product
does and what data it touches. Section 6 is a code cross-reference. Section 7 is
the mapping from the engineering "open items" (O-1…O-3) to the rows here.

**Status legend:** ☐ open · ◐ built in code, decision still needed · ☑ done

---

## 1. Contracts

| # | Item | Owner | Status |
|---|------|-------|--------|
| **B-1** | **Business Associate Agreement (BAA) with every subprocessor that can receive PHI.** Known subprocessors: the hosting / database provider (Supabase), the AI model provider (Anthropic — receives PHI from agents 03, 05, 07; see §5.3), and any future email/SMS, telemetry, or error-tracking vendor. No PHI may flow to a vendor without a signed BAA. | Legal | ☐ |
| **B-2** | **Customer-facing BAA** — Foresight (as Business Associate) ↔ each clinic (Covered Entity), executed before that clinic's data is loaded. | Legal + customer | ☐ |
| **B-3** | **Confirm the Anthropic commercial terms cover PHI use** — specifically: no training on API inputs/outputs, the zero/short data-retention configuration, and that a BAA is in place. Applies to agent 07 (claim text), agent 03 (insurance-card **images**), and agent 05 (patient name + service names). | Legal | ☐ |
| **B-4** | **Published, current subprocessor list** available to customers. | Compliance | ☐ |
| **B-5** | **Data Processing Addendum / state-law addenda** as required by the customer's state (e.g. CA, TX, WA consumer-health-data laws) beyond HIPAA. | Legal | ☐ |

## 2. Compliance decisions (several engineering tasks are blocked on these)

| # | Item | Owner | Status |
|---|------|-------|--------|
| **C-1** | **PHI retention schedule** — set a retention period for each data class: insurance-card **images**, extracted card **text**, eligibility results, prior-auth request/response payloads, coordination-of-benefits coverage rows, cost estimates, `activity_log`, `escalations`, and database backups. The code currently uses a **provisional 90-day** post-confirmation retention for card images (`ocr.CONFIRM_IMAGE_RETENTION_DAYS`) as a placeholder. | Compliance | ◐ |
| **C-2** | **Card-image purge cadence.** The deletion job is built, tested, and idempotent (`scripts/purge_expired_card_images.py` / `app/card_retention.py`), but it is **run manually** — there is no scheduler in the system yet. Once C-1 sets the retention period, decide the mechanism (cron, a scheduled Supabase Edge Function, a worker) **and the frequency**, and the acceptable window between "past retention" and "actually deleted". These are compliance calls, not engineering calls. | Compliance → Eng | ◐ |
| **C-3** | **No Surprises Act Good Faith Estimate disclaimer — legal sign-off.** `NSA_GFE_DISCLAIMER` (in `backend/app/agents/cost_estimate.py`) is a faithful draft of the CMS model language and carries a `REQUIRES LEGAL SIGN-OFF BEFORE PRODUCTION` comment. Counsel must review and approve the **exact wording** before any real patient receives an estimate. It currently covers: what a GFE is; that it excludes unknown/unexpected costs; the federal patient-provider dispute right at **$400 or more** over the estimate, within **120 days** of the bill; that it is not a contract; keep a copy; the cms.gov/nosurprises pointer. After sign-off, bump `NSA_GFE_DISCLAIMER_VERSION` — stored estimates snapshot the text + version, so historical estimates are unaffected. | Legal | ◐ |
| **C-4** | **No Surprises Act — scope and process review.** Confirm: (a) the flat self-pay price list is an acceptable basis for a GFE in the target state(s); (b) the "primary item + services reasonably expected with it" requirement is met by staff selecting the codes (the document says it covers only the listed services); (c) the timing rules (GFE furnished within the required window of scheduling / on request) are handled operationally; (d) whether a co-provider / co-facility GFE aggregation obligation applies. | Compliance | ☐ |
| **C-5** | **Coordination-of-benefits determination — disclaimer & state variation.** Agent 04 outputs a primary/secondary ordering with the deciding NAIC rule cited. It is advisory and staff can override, but it is a payer-facing determination. Confirm: (a) the NAIC-model rules as implemented (R0–R7, see `docs/agents/04-cob-agent.md` §6) match the target state(s) — most have adopted the model regulation but not all; (b) the out-of-scope simplifications are acceptable (no court-decree/custody capture — birthday rule is the default; no employer-size capture for the Medicare-Secondary-Payer 20-employee rule — flagged in the rationale text); (c) the UI wording makes clear this is guidance to verify with the payers, not a final coverage decision. | Compliance | ☐ |
| **C-6** | **Insurance-card OCR — accuracy and human-review posture.** Agent 03 extracts card fields with Claude vision. It is structurally a **data-entry aid**: nothing is written to a patient/appointment record without an explicit human confirmation, low-confidence fields are surfaced for correction, and the model is instructed never to guess. Confirm this human-in-the-loop design is sufficient and that there is no expectation the extraction is authoritative. | Compliance | ☑ (design) / ☐ (sign-off) |
| **C-7** | **Breach-notification procedure** documented, owned, and tested (who is notified, in what window, by whom). | Compliance | ☐ |
| **C-8** | **Minimum-necessary review** — the three roles (`platform_admin`, `clinic_admin`, `staff`) and what each can see (`docs/architecture.md` §4), checked against each customer's actual staffing. Row-Level Security enforces tenant isolation; role scoping within a tenant is coarser. | Compliance | ☐ |
| **C-9** | **Audit logging adequacy** — `activity_log` records agent and human actions per tenant. Confirm it captures what an audit requires, that its retention matches C-1, whether it must be immutable/append-only at the database level, and whether clinic admins must be able to view it (no UI for it today). | Compliance | ☐ |
| **C-10** | **Patient notice / consent** — whether the clinic's existing Notice of Privacy Practices covers Foresight's processing (AI phrasing of estimates, vision OCR of cards, automated claim analysis), or whether additional notice/consent is needed. | Legal | ☐ |
| **C-11** | **Individual rights** — how a patient's request for access to / amendment of / accounting of disclosures for data held in Foresight is fulfilled (there is no patient-facing access path; requests come via the clinic). | Compliance | ☐ |
| **C-12** | **De-identification standard** for any future analytics/reporting or model evaluation that uses real data — which method (Safe Harbor vs. Expert Determination) and who signs off. | Compliance | ☐ |

## 3. Security & operational readiness (engineering, but must be done before real PHI)

| # | Item | Owner | Status |
|---|------|-------|--------|
| **S-1** | Hosting/database on a **paid tier with point-in-time recovery and backups**; backup retention set to match C-1. | Eng | ☐ |
| **S-2** | **Encryption at rest** confirmed for the database **and** the private `card-scans` object-storage bucket; TLS enforced on every connection. | Eng | ☐ |
| **S-3** | **Secrets management** — `ANTHROPIC_API_KEY` and `SUPABASE_SERVICE_ROLE_KEY` moved out of `.env` files into a managed secret store; rotation procedure defined. | Eng | ☐ |
| **S-4** | **Access logging + alerting on the service-role key path** (the backend's privileged database access that bypasses Row-Level Security for agent writes — `docs/architecture.md` §3.4). | Eng | ☐ |
| **S-5** | **Independent security review / penetration test** of the authentication + Row-Level Security tenant boundary. Automated cross-tenant isolation tests exist (`tests/agent_isolation_test.py`, `tests/isolation_test.py`); an external review is still required. | Eng + external | ☐ |
| **S-6** | **Incident-response runbook**. | Eng | ☐ |
| **S-7** | **Data-deletion on customer offboarding** — a tested procedure to remove one tenant's data (organization delete is listed as not-built in `docs/architecture.md` §7). | Eng | ☐ |
| **S-8** | **Backup handling** — backups contain PHI; confirm they are encrypted, access-controlled, covered by the hosting BAA, and purged on the C-1 schedule. | Eng | ☐ |

## 4. Data-flow specifics to confirm with counsel

| # | Item | Status |
|---|------|--------|
| **D-1** | **Insurance-card images leave the database boundary** — 03 base64-encodes the uploaded image and sends it to the Anthropic API for extraction. The raw bytes are stored only in the private storage bucket, never in a table, and are deleted on the C-1 schedule; only the extracted text persists long-term. Confirm this transmission is covered by B-1/B-3. | ☐ |
| **D-2** | **Claim data to the AI** — agent 07 sends claim identifiers, patient name, billed amount, payer, and rule-engine findings to Anthropic for plain-language explanation. Grounded prompt; no PHI beyond what's needed. Confirm coverage. | ☐ |
| **D-3** | **Estimate data to the AI** — agent 05 sends the clinic name, patient name, service **names** (not prices), and the computed total to Anthropic for phrasing. The model never receives or returns a per-line price and cannot alter the total. Confirm coverage. | ☐ |
| **D-4** | **Eligibility & prior-auth are simulations today** — 01 and 02 do **not** contact a real clearinghouse or payer; they are deterministic simulations over seeded config (`docs/architecture.md` §7). When real integrations are added, each payer/clearinghouse connection needs its own BAA/trading-partner agreement — out of scope for this checklist but noted so it is not forgotten. | ☐ |
| **D-5** | **Marketing site "Book a demo" form** collects a name, email, and free-text message — business contact information, not PHI. Confirm it is handled under the normal privacy policy and kept separate from any tenant data (it is: `demo_requests` table, no `organization_id`). | ☐ |

---

## 5. Context for the reviewer

### 5.1 What Foresight is
A multi-tenant SaaS application for medical clinics that automates parts of the
revenue cycle and front-desk workflow. Each clinic is an isolated tenant;
tenant isolation is enforced in the database by PostgreSQL Row-Level Security on
every table.

### 5.2 What data it holds (all PHI unless noted)
- Patient name, date of birth, member ID (no dedicated patient table — patients
  are identified softly by name + DOB within a clinic).
- Appointments, eligibility check results, prior-authorization requests.
- Insurance coverage records (payer, plan type, subscriber, dates) — agent 04.
- Insurance-card images (short-lived) + extracted card text — agent 03.
- Claims, billed amounts, claim issues, AI explanations — agents 06/07/08.
- Cost estimates / Good Faith Estimates — agent 05.
- Per-tenant activity log and escalations.
- User accounts (clinic staff) — name, email, role. Not patient data.
- Marketing demo requests — business contact info, not PHI, no tenant link.

### 5.3 Where AI is used, and what it receives
| Agent | Purpose | Sent to the model | Guardrail |
|-------|---------|-------------------|-----------|
| 03 — OCR | read an insurance card | the card **image** | extraction only; a human confirms before any write; model told never to guess |
| 05 — cost estimate | phrase a Good Faith Estimate | clinic + patient name, service names, computed total | the number is computed first, in pure code; the model cannot change it |
| 07 — reasoning | explain claim issues | claim + payer + rule-engine findings | grounded prompt; cannot invent issues, codes, amounts |
| 04 — COB, 06 — rules, 01/02 — eligibility/prior-auth | — | **nothing** — pure deterministic code, no model call | — |

### 5.4 Regulatory surface
- **HIPAA** (Privacy, Security, Breach Notification) — Foresight is a Business
  Associate of each clinic.
- **No Surprises Act, 45 CFR §149.610 / §149.620** — the Good Faith Estimate
  for uninsured/self-pay patients and the patient-provider dispute process
  (agent 05).
- **NAIC Coordination of Benefits Model Regulation** (as adopted by each state)
  — the primary/secondary ordering rules (agent 04).
- **State consumer-health-data / privacy laws** where the customer operates.

### 5.5 The human-in-the-loop principle (already built)
No agent takes an outward-facing or irreversible action on its own. Claim
actions require human approval; card-scan extractions require human
confirmation; COB and cost estimates are advisory documents a human reviews.
Emergency care is never gated by any agent. See `docs/architecture.md` §1.

---

## 6. Code cross-reference

| Concern | Where in code |
|---------|---------------|
| Provisional card-image retention constant | `backend/app/agents/ocr.py` — `CONFIRM_IMAGE_RETENTION_DAYS` |
| Card-image purge job | `backend/app/card_retention.py`, `scripts/purge_expired_card_images.py`, migration `20260907000002` (`card_scans.image_purged_at`) |
| NSA disclaimer + version + dispute constants | `backend/app/agents/cost_estimate.py` — `NSA_GFE_DISCLAIMER`, `NSA_GFE_DISCLAIMER_VERSION`, `GFE_DISPUTE_THRESHOLD_USD`, `GFE_DISPUTE_WINDOW_DAYS` |
| NSA self-pay gate | `backend/app/agents/cost_estimate.py` — `gate_reason()`; enforced in `backend/app/routers/cost_estimates.py` |
| COB rule ladder | `backend/app/agents/cob.py` — `determine_cob()`; spec `docs/agents/04-cob-agent.md` §6 |
| OCR human-confirm gate | `backend/app/routers/card_scans.py` — only `…/confirm` writes an appointment field |
| Tenant isolation (RLS) | every migration's `select public.enable_tenant_isolation(...)`; `docs/architecture.md` §3 |
| Service-role (RLS-bypass) path | `backend/app/agents/db.py`, `backend/app/storage.py`; `docs/architecture.md` §3.4 |
| Roles & visibility | `docs/architecture.md` §4 |

## 7. Engineering "open items" → rows here

The per-phase docs carry an open-items table (`docs/PHASE-4.md`). Anything that
gates **real PHI** is lifted here:

| Open item | This checklist |
|-----------|----------------|
| **O-1** — schedule the card-image purge job | **C-2** |
| **O-2** — `CONFIRM_IMAGE_RETENTION_DAYS = 90` is provisional | **C-1** |
| **O-3** — `NSA_GFE_DISCLAIMER` needs legal sign-off | **C-3** |

## 8. How this document is maintained

- Any code comment that says "provisional", "needs review", or "before
  production" **must** have a matching row here or in a `PHASE-N.md` open-items
  table.
- `PHASE-N.md` holds phase-specific open items; anything that gates real PHI is
  lifted into this file.
- When an item is resolved, mark it ☑ and record the sign-off (who, when).
