-- ============================================================================
-- Foresight — Phase 5: appeals (11-appeals-agent)
-- ----------------------------------------------------------------------------
-- Closes the loop on the claims pipeline. When the payer DENIES a claim,
-- 11-appeals-agent drafts an appeal letter grounded strictly in evidence
-- already in the system (06's claim_issues, a recorded denial reason, actions
-- the team already took), a human reviews and approves the send, 11.submit does
-- a SIMULATED submission, and a deterministic resolution comes back. A won
-- appeal is the ONE thing that transitions a claim out of `denied` -> `paid`.
--
-- 11 IS a Commander agent (unlike the synchronous 03/04/05). It is a FOURTH
-- disjoint Commander family (AP1-AP12, docs/agents/00-commander.md §14),
-- dispatched before R1 like eligibility (E1-E7) and prior-auth (A1-A11).
-- commander_test.py / eligibility_commander_test.py / prior_auth_commander_test.py
-- still pass unchanged.
--
-- TWO STRUCTURAL INVARIANTS (00-commander.md §14.3), hard-raised in
-- orchestrator.handle_appeal:
--   * the ONLY rule routing to 11.submit is AP6, gated on an
--     `appeal_submission_approved` trigger + `appeal.status == 'drafted'` — no
--     appeal is submitted without a recorded human approval;
--   * the ONLY rule with a non-null next_status is AP9 (`denied` -> `paid`), and
--     only on a won resolution after that approval.
--
-- Same tenancy rule as every prior phase: organization_id not null +
-- select public.enable_tenant_isolation('public.appeals'). Agent writes use the
-- service-role key and filter every query by the organization_id resolved from
-- the triggering claim.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- claims.denial_reason — what the payer said, when we have it. One of 11's
-- grounding inputs. Set by the seed / a future payer-sync. NULL is allowed — an
-- appeal can still be drafted from the rule-engine issues alone.
-- ----------------------------------------------------------------------------
alter table public.claims add column denial_reason text;

comment on column public.claims.denial_reason is
  'The payer''s stated reason for a denied claim, when recorded (set by the seed / a future payer-sync). One of 11-appeals-agent''s grounding inputs. NULL allowed.';

-- ----------------------------------------------------------------------------
-- Enum
-- ----------------------------------------------------------------------------
create type public.appeal_status as enum (
  'pending',              -- row created, 11 has not drafted yet
  'drafted',              -- 11 wrote a grounded letter — awaiting a human's approval to send
  'insufficient_basis',   -- 11 found nothing citable — no letter written, routed to a human (AP3)
  'submission_declined',  -- a human reviewed the draft and chose not to send it
  'submitting',           -- a human approved; 11.submit is running (simulated)
  'submitted',            -- sent to the (simulated) payer, awaiting their decision
  'appeal_approved',      -- (simulated) payer reversed the denial in full
  'appeal_partial',       -- (simulated) payer reversed part of it
  'appeal_denied',        -- (simulated) payer upheld the denial
  'error'                 -- the drafting model could not be reached (letter_text = '')
);

-- ----------------------------------------------------------------------------
-- appeals — one appeal attempt against a denied claim.
--
-- SIDECAR: claims has no FK to appeals. A denial can exist with no appeal, or
-- with an appeal in any status, and nothing else breaks. A second-level appeal
-- APPENDS a new row (history, like eligibility_checks / prior_authorizations);
-- previous_appeal_id chains them.
-- ----------------------------------------------------------------------------
create table public.appeals (
  id                 uuid primary key default gen_random_uuid(),
  organization_id    uuid not null references public.organizations (id) on delete cascade,
  claim_id           uuid not null references public.claims (id) on delete cascade,
  previous_appeal_id uuid references public.appeals (id) on delete set null,   -- append-only second-level chain
  denial_reason      text,                                -- snapshot of claims.denial_reason at draft time
  grounds            jsonb not null default '[]'::jsonb,   -- [{source, ref, detail}] — the cited REAL evidence
  has_basis          boolean not null default false,
  letter_text        text not null default '',            -- the drafted appeal letter ('' if insufficient_basis / error)
  status             public.appeal_status not null default 'pending',
  model              text,                                -- which model drafted it (null if not drafted)
  submission_payload jsonb not null default '{}'::jsonb,   -- the simulated submission packet
  resolution_payload jsonb not null default '{}'::jsonb,   -- {outcome, bucket, reversed_amount, responded_at}
  reviewed_by        uuid references auth.users (id) on delete set null,   -- the human who approved / declined the send
  reviewed_at        timestamptz,
  resolved_at        timestamptz,
  created_at         timestamptz not null default now()
);

create index appeals_organization_id_idx on public.appeals (organization_id);
create index appeals_claim_id_idx        on public.appeals (claim_id, created_at);
create index appeals_status_idx          on public.appeals (organization_id, status);

comment on table public.appeals is
  'One appeal attempt against a denied claim (Phase 5). Sidecar: claims has no FK to appeals. The letter is grounded strictly in this claim''s real rows (11-appeals-agent §2.1); it is never sent without a human approving through POST /api/appeals/{id}/approve-submission. A won appeal is the only thing that transitions claims.status (denied -> paid). Tenant-scoped.';
comment on column public.appeals.grounds is
  'The citable evidence the appeal letter is built from, collected by the pure appeal_basis() BEFORE the drafting model is called. Each item {source, ref, detail} is copied verbatim from a real claim_issues / recommendations row or claims.denial_reason. The model never sees anything else.';

-- ============================================================================
-- Tenant isolation — the standard Phase 0 guard
-- ============================================================================
select public.enable_tenant_isolation('public.appeals');

-- ============================================================================
-- API surface: reads only for client roles; every write is backend-mediated
-- (Phase 1-4 discipline). RLS narrows the grant to the caller's own tenant.
-- Appeals are claim-scoped, so activity_log / escalations reuse claim_id (with
-- context.appeal_id) — no new FK column, unlike the appointment-scoped
-- eligibility / prior-auth families.
-- ============================================================================
revoke all on public.appeals from anon, authenticated;
grant  select on public.appeals to authenticated;
