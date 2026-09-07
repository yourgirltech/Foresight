# 03 — Insurance Card OCR Agent

_Spec. Written before implementation, per the Phase 4 plan. Cross-check this
document before any Phase 4 / agent-03 code is written._

_Status: **BUILT** (2026-09-07). Companion: [`../PHASE-4.md`](../PHASE-4.md).
Implementation: migrations `20260907000001_insurance_card_ocr.sql` +
`20260907000002_card_scan_image_purge.sql`, `backend/app/agents/ocr.py`,
`backend/app/storage.py`, `backend/app/card_retention.py`,
`backend/app/routers/card_scans.py`, `scripts/seed_card_scans.py`,
`scripts/purge_expired_card_images.py`,
`frontend/src/pages/{FrontDeskPage,CardScanReviewPage}.tsx`. Tests:
`tests/ocr_gate_test.py` (pure grid), `tests/ocr_live_test.py` (`--live`),
`tests/e2e_card_scan_test.py`, `tests/card_scan_purge_test.py`, plus the
card-scan section of `tests/agent_isolation_test.py`.
`bash scripts/run_ocr_proof.sh`.
The 90-day post-confirm retention is `ocr.CONFIRM_IMAGE_RETENTION_DAYS` — a
provisional demo/pilot default flagged for compliance review before production._

---

## 1. What 03 is

The **Insurance Card OCR Agent** turns a photo of a patient's insurance card into
structured coverage fields the front desk can check and save — instead of
retyping them by hand from a phone photo at check-in.

It is a **real feature from day one**, not a simulation. It calls **Claude's
vision capability directly** (`client.messages.create` with an image content
block — the same `ANTHROPIC_API_KEY` already wired for 07). There is no OCR
vendor to choose and no fake response: the model reads the actual uploaded image.

| # | Agent | Kind | LLM? | Job |
|---|-------|------|------|-----|
| 03 | **ocr-agent** | vision extraction | **yes** (Claude vision) | uploaded card image → `{member_id, group_number, payer_name, plan_type}`, each with a confidence flag; low-confidence / illegible fields are surfaced for a human, never guessed |

## 2. The non-negotiable: this is a data-entry aid, not an autonomous action

> A low-confidence or ambiguous extraction **never auto-fills** an appointment or
> patient record. Every extracted value is a **draft** that a human confirms or
> corrects before it is saved as authoritative.

Enforced by the structure, not by discipline:

| # | Mechanism | Where |
|---|-----------|-------|
| O1 | **`card_scans` is a sidecar table.** No appointment or patient field has a `not null` FK to it. A scan can exist, be wrong, or never be reviewed, and nothing downstream breaks. | §4 |
| O2 | **The extract step writes only to `card_scans`.** It never touches `appointments`. The only path that writes `appointments.patient_member_id` (etc.) is `POST /api/card-scans/{id}/confirm`, which requires an explicit human action and the human's corrected values. | §7 |
| O3 | **`applied_to_appointment` starts false and only the confirm endpoint flips it.** A query can always tell which appointment fields came from a *reviewed* scan vs. a raw extraction. | §4 |
| O4 | **The vision prompt forbids inference.** "Extract only what is visibly printed. If a field is not clearly legible, return `null` with `legible: false` — never infer it, never use a typical or common value." A blurry card yields `null`s, not plausible-looking fabrications. | §6 |
| O5 | **The confidence gate is deterministic and testable.** `classify_extraction()` (§6.4) is a pure function: any field `null`, `legible: false`, or below the confidence floor → `needs_review`. The model's own confidence never silently promotes a field to "trusted". | §6.4 |
| O6 | **Not a Commander agent.** 03 is a synchronous request/response tool. It routes nothing, advances no workflow state, and has no trigger family. `commander.py` is untouched by Phase 4. | `../PHASE-4.md` |

## 3. Where 03 sits

```
front desk photographs a card
        │
   POST /api/card-scans   (multipart: image + optional appointment_id)
        │
   1. store the image in Supabase Storage (private bucket)  ── card_scans.image_path
   2. Claude vision call (strict prompt)                     ── card_scans.extracted_fields + field_confidence
   3. classify_extraction()  →  status  ∈ {extracted, needs_review}
        │
   return the scan + the fields, each flagged
        │
   ── UI: image preview  ║  editable fields (low-confidence flagged amber)
        │
   human edits / accepts
        │
   POST /api/card-scans/{id}/confirm   { corrected fields, appointment_id }
        │
   writes appointments.patient_member_id / payer_id / ...     ── the ONLY write-back path
   card_scans.status = confirmed ; applied_to_appointment = true
```

No arrow writes an appointment field except the human-triggered confirm.

## 4. Data model

New migration `supabase/migrations/2026090X000001_insurance_card_ocr.sql`.

### 4.1 Supabase Storage — a private bucket

```sql
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('card-scans', 'card-scans', false, 10485760,
        array['image/jpeg','image/png','image/webp','image/heic'])
on conflict (id) do nothing;

-- No client access at all. The backend reads/writes objects with the service
-- role; the browser never touches Storage directly (Phase 2–3 discipline).
-- (No policies on storage.objects for this bucket => authenticated/anon denied.)
```

Object path convention: `<organization_id>/<card_scan_id>.<ext>`. The backend
resolves `organization_id` from the session before writing.

### 4.2 `card_scans`

```sql
create type public.card_scan_status as enum (
  'pending',       -- row created, vision call not finished
  'extracted',     -- all fields high-confidence — pre-fill and let a human confirm
  'needs_review',  -- >=1 field null / illegible / low-confidence — a human must supply it
  'confirmed',     -- a human confirmed; values written back to an appointment
  'rejected',      -- a human discarded this scan
  'error'          -- the vision call failed (see extracted_fields.error)
);

create table public.card_scans (
  id                     uuid primary key default gen_random_uuid(),
  organization_id        uuid not null references public.organizations (id) on delete cascade,
  appointment_id         uuid references public.appointments (id) on delete set null,  -- nullable BY DESIGN
  patient_name           text not null default '',      -- snapshot / free text at upload
  image_path             text not null,                 -- storage object path (never the bytes)
  image_mime             text not null,
  extracted_fields       jsonb not null default '{}'::jsonb,   -- {member_id, group_number, payer_name, plan_type}
  field_confidence       jsonb not null default '{}'::jsonb,   -- {member_id: {confidence, legible}, ...}
  status                 public.card_scan_status not null default 'pending',
  model                  text,                          -- which model did the extraction
  reviewed_by            uuid references auth.users (id) on delete set null,
  reviewed_at            timestamptz,
  applied_to_appointment boolean not null default false,
  created_at             timestamptz not null default now()
);
create index card_scans_organization_id_idx on public.card_scans (organization_id);
create index card_scans_appointment_id_idx  on public.card_scans (appointment_id, created_at);
create index card_scans_status_idx          on public.card_scans (organization_id, status);

select public.enable_tenant_isolation('public.card_scans');
revoke all on public.card_scans from anon, authenticated;
grant  select on public.card_scans to authenticated;   -- UI reads; all writes backend-mediated
```

### 4.3 The four extracted fields

Exactly the four the review named — nothing inferred beyond the card face:

| field | what it is |
|-------|-----------|
| `member_id` | the subscriber / member ID printed on the card |
| `group_number` | the group / plan group number |
| `payer_name` | the insurer name as printed (e.g. "Blue Cross Blue Shield of Texas") |
| `plan_type` | the plan type as printed if present (HMO / PPO / EPO / POS / Medicare Advantage / Medicaid) — `null` if not on the card |

## 5. Settings

```python
# app/config.py — new field
ocr_model: str = "claude-opus-5"   # override OCR_MODEL; defaults to reasoning_model's default
# confidence floor below which a field forces needs_review
ocr_confidence_floor: str = "medium"   # one of: high | medium | low
```

## 6. The extraction contract

### 6.1 Interface

```python
# backend/app/agents/ocr.py
from dataclasses import dataclass

class OcrUnavailable(RuntimeError):
    """The vision model could not be reached (missing key, API error)."""

@dataclass(frozen=True)
class CardExtraction:
    fields: dict            # {member_id, group_number, payer_name, plan_type} — values or None
    confidence: dict        # {<field>: {"confidence": "high|medium|low", "legible": bool}}

async def extract(image_bytes: bytes, mime: str) -> CardExtraction: ...
```

### 6.2 The vision call

`anthropic.AsyncAnthropic(...).messages.create(model=settings.ocr_model, ...)`,
user content = an `image` block (base64) **then** a text block, with structured
outputs (`output_config: {format: {type: "json_schema", schema: <the schema>}}`)
so the response validates exactly. Fallback: if `output_config` errors, parse the
first JSON object out of the text (07's `_extract_json`).

### 6.3 System prompt (verbatim intent)

> You extract insurance-card fields for a medical front desk. You are given one
> image of an insurance card. Return ONLY these four fields: `member_id`,
> `group_number`, `payer_name`, `plan_type`.
>
> Rules:
> - Extract **only what is visibly printed on this card**. Read it; do not
>   reason about what it "should" be.
> - If a field is not present on the card, or is not clearly legible (blur,
>   glare, cropping, low resolution), set its value to `null` and `legible:
>   false`. **Never infer, never guess, never substitute a typical or common
>   value.** A wrong value is far worse than `null`.
> - For each field, report `confidence` as `high` (clearly legible, no
>   ambiguity), `medium` (legible but some ambiguity — a character that could be
>   0/O, 1/I), or `low` (barely legible).
> - `payer_name` is the insurer's name as printed. Do not expand abbreviations
>   or add a state/region that isn't printed.
> - Do not read any other card (pharmacy, dental) or the back of the card unless
>   it is the image provided.

### 6.4 The confidence gate — `classify_extraction()` (pure, exhaustively tested)

```python
FLOOR_RANK = {"low": 0, "medium": 1, "high": 2}

def classify_extraction(ext: CardExtraction, *, floor: str = "medium") -> str:
    """pending -> 'extracted' if EVERY field is present, legible, and at/above
    the confidence floor; otherwise 'needs_review'. Deterministic. §O5."""
    for name in ("member_id", "group_number", "payer_name", "plan_type"):
        value = ext.fields.get(name)
        meta  = ext.confidence.get(name, {})
        # plan_type is optional — a null plan_type with legible:false-because-absent is OK
        if name == "plan_type" and value is None and meta.get("absent") is True:
            continue
        if value is None or meta.get("legible") is not True:
            return "needs_review"
        if FLOOR_RANK.get(meta.get("confidence", "low"), 0) < FLOOR_RANK[floor]:
            return "needs_review"
    return "extracted"
```

`extracted` still requires a human confirm before write-back — it only means the
UI can safely pre-fill every field. `needs_review` means at least one field is a
blank a person must fill.

## 7. Backend + UI

### 7.1 Endpoints (`backend/app/routers/card_scans.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/card-scans` | multipart `image` (+ optional `appointment_id`, `patient_name`). Stores the image, runs `extract()`, runs `classify_extraction()`, returns the row. On a vision error → `status = error`, 200 with the error surfaced (not a 5xx). |
| `GET` | `/api/card-scans` | scans in the caller's clinic (RLS), newest first; `?status=` filter |
| `GET` | `/api/card-scans/{id}` | one scan + a short-lived signed URL for the image (backend mints it via the Storage API) |
| `POST` | `/api/card-scans/{id}/confirm` | body `{member_id?, group_number?, payer_name?, plan_type?, appointment_id?}` — the human's final values. Writes `appointments.patient_member_id` (and matches / sets `payer_id` by `payer_name` where possible) **only if `appointment_id` is given and visible to the caller**; sets `status = confirmed`, `reviewed_by`, `applied_to_appointment = true`. |
| `POST` | `/api/card-scans/{id}/reject` | `status = rejected`, `reviewed_by` |

Adds `python-multipart` to `backend/requirements.txt` (FastAPI needs it for
`UploadFile`). Image size capped at 10 MB (bucket limit); MIME allow-list matches
the bucket.

### 7.2 UI (`frontend/src/pages/CardScanPage.tsx` + a review view)

- New nav item **Card Scan** (or fold under Front Desk — §9 open decision).
- **Upload**: a drop zone / file picker + camera capture (`<input capture>`), an
  optional appointment picker.
- **Review**: the card image on the left (signed URL, `object-contain`), the four
  fields on the right as editable inputs. Each field shows its confidence; `low`
  / illegible / null fields are amber with a "not legible — please type it from
  the card" hint. A **Confirm & apply** button (disabled until every field has a
  value) and a **Reject** button.
- After confirm: a success line naming which appointment was updated.

Types → `frontend/src/lib/types.ts` (`CardScan`, `CardScanStatus`, `CardExtraction`).

## 8. Test plan

### `tests/ocr_gate_test.py` (pure, no stack, no key) — the exhaustive part

`classify_extraction()` over the **full grid**: for each of the 4 fields ×
`value ∈ {present, None}` × `legible ∈ {true, false}` × `confidence ∈ {high,
medium, low}` × `floor ∈ {high, medium, low}` — assert:

- deterministic (same input → same status, twice);
- **any** field `None` or `legible: false` ⟹ `needs_review`;
- **any** field below `floor` ⟹ `needs_review`;
- all fields present + legible + ≥ floor ⟹ `extracted`;
- the `plan_type`-absent carve-out behaves (absent + flagged → not a blocker;
  absent + not flagged → `needs_review`);
- a couple of realistic scenarios spelled out by name (`test_glare_on_member_id_forces_review`,
  `test_clean_card_is_extracted`, `test_medium_confidence_ambiguous_digit_at_high_floor_reviews`).

### `tests/ocr_live_test.py` (opt-in: `--live`, needs the key — skipped otherwise)

Synthetic card fixtures in `tests/fixtures/cards/` — **clearly fake** (made-up
payer "Northwind Health", obviously synthetic member IDs, a watermark). Cases:

| fixture | asserted |
|---------|----------|
| `clean.png` | all four fields extracted; `member_id` matches the known synthetic value exactly; status `extracted` |
| `blurry-member-id.png` | `member_id` is `null` + `legible: false` (NOT a fabricated value); status `needs_review` |
| `no-plan-type.png` | `plan_type` is `null` and flagged absent; the other three extracted |
| `wrong-card-back.png` | fields the model can't see are `null`, not guessed |

Never asserts an exact value for a field it deliberately made illegible — the
point is that 03 returns `null`, not a plausible guess.

### `tests/agent_isolation_test.py` (extended)

A card-scan confirm over Clinic A's appointment never reads or writes a Clinic B
row (`card_scans`, `appointments`), and the image object path is under A's org
prefix.

## 9. Open decisions — resolve at review

1. **Nav placement** — a top-level **Card Scan** item, or fold the upload/review
   into the existing **Front Desk** stub. → recommend **Front Desk** (that stub's
   blurb is literally "check-in queue, intake forms" — card capture belongs
   there), promoting Front Desk from a stub to a real page.
2. **Structured outputs vs. text+parse** for the vision response. → recommend
   **structured outputs** (`output_config.format`) — strict field extraction is
   exactly its use case; keep 07's text-parse as the fallback path.
3. **`payer_id` matching on confirm** — fuzzy-match `payer_name` to an existing
   `payers` row, or always leave `payer_id` for the human. → recommend: attempt
   an exact (case-insensitive, trimmed) match only; otherwise leave `payer_id`
   null and show "payer not in your directory — add it in Settings".
4. **Image retention** — keep the stored image indefinitely, or delete it on
   `confirmed` / `rejected` (keep only the extracted text). → recommend **delete
   on reject; keep 90 days on confirm** then a later cleanup job (note: real PHI —
   minimise retention). Flag for a retention-policy decision.
5. **`ocr_confidence_floor` default** — `medium` proposed. Tune against the live
   fixtures at review.

### 9.1 As built (2026-09-07)

1. **Front Desk** — the `front-desk` route is now `FrontDeskPage` (list +
   capture) + `front-desk/:id` `CardScanReviewPage`. No new nav item.
2. **Structured outputs** — `extract()` calls with
   `output_config={"format": {"type": "json_schema", "schema": ...}}` and falls
   back to a plain call + `_extract_json` on `TypeError` / `BadRequestError`.
   Verified live against `claude-opus-5`.
3. **Exact payer match only** — confirm matches `payer_name` case-insensitively
   / trimmed to a `payers` row; no match ⇒ `payer_id` left null.
4. **Retention** — reject deletes the Storage object immediately;
   confirm stamps `image_retain_until = now + CONFIRM_IMAGE_RETENTION_DAYS`
   (90, **provisional — compliance review required**). The purge job
   (`scripts/purge_expired_card_images.py`, `app.card_retention`,
   migration `20260907000002`, `tests/card_scan_purge_test.py`) deletes
   past-retention objects and stamps `image_purged_at` — built + idempotent, but
   **run manually** until there is a scheduler (PHASE-4.md open item O-1).
5. **Floor `medium`** — `ocr_confidence_floor` default `medium`; overridable via
   `OCR_CONFIDENCE_FLOOR`.
