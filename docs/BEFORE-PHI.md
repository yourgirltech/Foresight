# Before real PHI touches Foresight — the non-engineering gate

Foresight today runs on **synthetic data only**. Everything in this checklist
must be resolved *before* a single real patient record, insurance card, or
claim enters the system — whether in a pilot, a customer demo with real data, or
production.

These are **not engineering tasks** (or not *only* engineering tasks): each
needs a decision from legal / compliance / the customer, and several
engineering tasks below are small but **cannot be scheduled correctly until that
decision is made**.

## 1. Contracts & legal

| # | Item | Owner | Blocks |
|---|------|-------|--------|
| B-1 | **Business Associate Agreement (BAA)** signed with every subprocessor that can see PHI — hosting (Supabase), the model provider (Anthropic), any email/SMS vendor, error/telemetry vendors. | Legal + customer | all real-PHI use |
| B-2 | **Customer BAA** — Foresight ↔ each clinic. | Legal | all real-PHI use |
| B-3 | Confirm the **Anthropic data-use terms** (no training on API traffic; retention window) cover both 07 (reasoning) and 03 (card images sent to the vision API). | Legal | 03, 07 with real data |
| B-4 | Subprocessor list published / kept current. | Compliance | production |

## 2. Compliance review (the decisions several engineering tasks wait on)

| # | Item | Owner | Related code |
|---|------|-------|--------------|
| C-1 | **Retention policy** — how long Foresight keeps each class of PHI: card images, extracted card fields, eligibility/PA payloads, activity_log, escalations. | Compliance | `ocr.CONFIRM_IMAGE_RETENTION_DAYS` (currently a provisional 90 — [PHASE-4 O-2](PHASE-4.md)) |
| C-2 | **Card-image purge cadence** — once C-1 sets the retention period, decide *how the purge runs* (cron, scheduled Supabase Edge Function, a worker) and *how often* (hourly / daily / …). The job itself is built and tested (`scripts/purge_expired_card_images.py`); only the schedule is open. Frequency and the acceptable "past retention but not yet deleted" window are a compliance call, not an engineering one. | Compliance → Eng | [PHASE-4 O-1](PHASE-4.md), `app/card_retention.py` |
| C-3 | **Good Faith Estimate / No Surprises Act disclaimer** — the `NSA_GFE_DISCLAIMER` text (05) must be the CMS-mandated language, legally reviewed and signed off, before any real patient sees an estimate. | Legal | [PHASE-4 O-3](PHASE-4.md), 05 spec §7 |
| C-4 | **Breach-notification procedure** documented and owned. | Compliance | — |
| C-5 | **Minimum-necessary review** of what each role can see (architecture §4) against the customer's actual staff structure. | Compliance | RLS policies |
| C-6 | **Audit-log requirements** — is `activity_log` sufficient (retention, immutability, what's captured), and does it need to be surfaced to clinic admins? | Compliance | `activity_log`, architecture §7 |

## 3. Security & operational readiness

| # | Item | Owner |
|---|------|-------|
| S-1 | Supabase project on a **paid tier with PITR / backups**; backup retention matches C-1. | Eng |
| S-2 | **Encryption at rest** confirmed for the DB *and* the `card-scans` Storage bucket; TLS enforced everywhere. | Eng |
| S-3 | Secrets management — `ANTHROPIC_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY` out of `.env` files and into a real secret store. | Eng |
| S-4 | Access logging + alerting on the service-role key path. | Eng |
| S-5 | Pen test / security review of the auth + RLS boundary. | Eng + external |
| S-6 | Incident response runbook. | Eng |

## How this list is maintained

- A code comment that says "provisional / needs review before production" **must**
  have a matching row here or in a phase doc's open-items table.
- Phase docs (`PHASE-N.md`) carry phase-specific open items; anything that gates
  *real PHI* is lifted up to this file.
