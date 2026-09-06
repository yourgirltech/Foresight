"""Foresight agent system.

A single Commander (00) — a pure decision node — routes one (state, trigger) to
the next specialist agent via an ordered rule table. Specialists:

  01  eligibility    simulated eligibility verification (deterministic, no LLM) — Phase 2
  06  analyzer       deterministic rule engine (no LLM)
  07  reasoning      plain-language explanation of the issue list (Claude API)
  08  recommendation issue list -> action + confidence band (deterministic)
  09  followup       execute an approved follow-up action (simulated send + record)
  10  reminder       execute an approved payer-reminder action
  12  escalation     safety net: log full context, flag for a human

The Commander has two disjoint rule tables: the claims pipeline (R1-R20) and the
eligibility block (E1-E7). See docs/agents/00-commander.md (§6 and §12) and
docs/agents/01-eligibility-agent.md.

Tenancy: docs/architecture.md §3 — no hardcoded tenant id anywhere; every agent
query is filtered by the organization_id taken from the triggering claim or
eligibility check.
"""
