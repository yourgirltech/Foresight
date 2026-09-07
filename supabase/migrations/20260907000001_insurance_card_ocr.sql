-- ============================================================================
-- Foresight — Phase 4: insurance card OCR (03-ocr-agent)
-- ----------------------------------------------------------------------------
-- A REAL feature, not a simulation: 03 sends the uploaded card image to Claude's
-- vision API and extracts what is visibly printed (member id / group / payer /
-- plan type), each with a confidence flag. docs/agents/03-ocr-agent.md is the
-- spec; keep them in lockstep.
--
-- THE NON-NEGOTIABLE (agent doc §2): this is a DATA-ENTRY AID, not an autonomous
-- action. A low-confidence or ambiguous extraction never auto-fills an
-- appointment or patient record — every value is a draft a human confirms or
-- corrects first. Enforced structurally:
--   * card_scans is a SIDECAR table — no appointment/patient field has a
--     not-null FK to it; a scan can be wrong, or never reviewed, and nothing
--     downstream breaks;
--   * the extract step writes ONLY to card_scans; the sole path that writes an
--     appointment field is POST /api/card-scans/{id}/confirm, which needs an
--     explicit human action + the human's corrected values;
--   * applied_to_appointment starts false and only the confirm endpoint flips it
--     — a query can always tell a reviewed value from a raw extraction.
--
-- 03 is NOT a Commander agent (docs/PHASE-4.md): a synchronous request/response
-- tool, no workflow state, no routing. commander.py is untouched by Phase 4.
--
-- Same tenancy rules as every prior phase: every table carries
--   organization_id uuid not null references public.organizations(id)
-- and is run through  select public.enable_tenant_isolation('public.<table>').
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Supabase Storage — a PRIVATE bucket for card images.
-- No policies on storage.objects for this bucket => anon + authenticated are
-- denied outright. The backend reads/writes objects with the service role only
-- (Phase 2-3 discipline: the browser never touches Storage directly). The raw
-- image bytes are NEVER stored in a table — card_scans holds only a reference.
-- ----------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'card-scans', 'card-scans', false, 10485760,
  array['image/jpeg', 'image/png', 'image/webp', 'image/heic']
)
on conflict (id) do update
  set public             = excluded.public,
      file_size_limit    = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- ----------------------------------------------------------------------------
-- Enum
-- ----------------------------------------------------------------------------
create type public.card_scan_status as enum (
  'pending',       -- row created, the vision call has not finished
  'extracted',     -- every field present, legible, and at/above the confidence floor — safe to pre-fill; still needs a human confirm
  'needs_review',  -- >=1 field is null / illegible / low-confidence — a human must supply it
  'confirmed',     -- a human confirmed; values were written back to an appointment
  'rejected',      -- a human discarded this scan
  'error'          -- the vision call failed (see extracted_fields.error)
);

-- ----------------------------------------------------------------------------
-- card_scans — one row per uploaded card image.
--
-- SIDECAR: appointment_id is nullable BY DESIGN. Nothing in the schema requires
-- a card_scans row to exist, or to be in any status, for anything else to work.
-- ----------------------------------------------------------------------------
create table public.card_scans (
  id                     uuid primary key default gen_random_uuid(),
  organization_id        uuid not null references public.organizations (id) on delete cascade,
  appointment_id         uuid references public.appointments (id) on delete set null,  -- nullable BY DESIGN
  patient_name           text not null default '',            -- free text captured at upload (a label, not authoritative)
  image_path             text not null,                       -- storage object path — NEVER the bytes
  image_mime             text not null,
  extracted_fields       jsonb not null default '{}'::jsonb,   -- {member_id, group_number, payer_name, plan_type} — values or null
  field_confidence       jsonb not null default '{}'::jsonb,   -- {<field>: {confidence: high|medium|low, legible: bool, absent?: bool}}
  status                 public.card_scan_status not null default 'pending',
  model                  text,                                -- which model did the extraction
  reviewed_by            uuid references auth.users (id) on delete set null,
  reviewed_at            timestamptz,
  applied_to_appointment boolean not null default false,      -- only the confirm endpoint sets this true
  image_retain_until     timestamptz,                         -- set on confirm (see ocr.CONFIRM_IMAGE_RETENTION_DAYS); a future cleanup job deletes the object after this
  created_at             timestamptz not null default now()
);

create index card_scans_organization_id_idx on public.card_scans (organization_id);
create index card_scans_appointment_id_idx  on public.card_scans (appointment_id, created_at);
create index card_scans_status_idx          on public.card_scans (organization_id, status);

comment on table public.card_scans is
  'One uploaded insurance-card image + its Claude-vision extraction (Phase 4). Sidecar to care: appointment_id nullable, never a precondition. A low-confidence extraction never auto-fills a record — POST /api/card-scans/{id}/confirm is the only write-back path. Tenant-scoped.';
comment on column public.card_scans.image_path is
  'Path of the object in the private card-scans Storage bucket (<organization_id>/<card_scan_id>.<ext>). The raw bytes are never stored in Postgres.';
comment on column public.card_scans.applied_to_appointment is
  'False until a human confirms the scan and its values are written back to an appointment. A raw extraction is always applied_to_appointment=false.';

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard
-- ============================================================================
select public.enable_tenant_isolation('public.card_scans');

-- ============================================================================
-- API surface: revoke from client roles, grant back only reads. RLS narrows the
-- grant to the caller's own tenant. Every card_scans write goes through a
-- backend endpoint that re-derives the caller's org server-side — NO client
-- write grants (Phase 2-3 discipline).
-- ============================================================================
revoke all on public.card_scans from anon, authenticated;
grant  select on public.card_scans to authenticated;
