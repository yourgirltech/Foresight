import { Link } from "react-router-dom";
import { ArrowRight, CalendarClock, FileSearch, ShieldCheck, UserCheck } from "lucide-react";

import { Capabilities } from "../Capabilities";
import { useDemoModal } from "../DemoModal";
import { Reveal } from "../motion";
import { ClaimsRiskVisual } from "../showcase";
import {
  CtaPanel,
  DisplayHeading,
  Eyebrow,
  FeatureCard,
  PageHeader,
  Section,
  btnDark,
  btnOutline,
} from "../components";

const STATUS_STYLE: Record<string, string> = {
  Live: "bg-success-soft text-success",
  "In build": "bg-accent-soft text-accent",
  Planned: "bg-surface-muted text-text-secondary",
};

function Capability({
  index,
  status,
  title,
  what,
  how,
}: {
  index: string;
  status: keyof typeof STATUS_STYLE;
  title: string;
  what: string;
  how: string[];
}) {
  return (
    <div className="grid gap-6 border-t border-border py-10 md:grid-cols-[auto_1fr] md:gap-10 lg:py-12">
      <span className="font-display text-2xl font-semibold text-text-muted">{index}</span>
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-xl font-semibold text-text-primary">{title}</h3>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status]}`}>
            {status}
          </span>
        </div>
        <p className="mt-3 max-w-2xl text-[15px] leading-[1.75] text-text-secondary">{what}</p>
        <ul className="mt-5 grid gap-2.5 sm:grid-cols-2">
          {how.map((h) => (
            <li key={h} className="flex gap-2.5 text-sm leading-[1.6] text-text-secondary">
              <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
              {h}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function ProductPage() {
  const { open } = useDemoModal();
  return (
    <>
      <PageHeader
        eyebrow="Product"
        title="Specialized AI agents for the healthcare revenue cycle."
        intro="Foresight is a set of focused AI agents coordinated by a single decision engine. Each one does one job well — find risk, verify coverage, draft the next step — and a person approves anything that has real consequences."
        chips={["Claims risk", "Eligibility", "Front desk", "Human approval"]}
        visual={
          <div className="fp-float-slow">
            <ClaimsRiskVisual />
          </div>
        }
      />

      {/* interactive showcase */}
      <Section inner="py-20 lg:py-24">
        <Reveal>
          <Eyebrow>Explore it</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 max-w-2xl text-3xl sm:text-4xl">
            One system, four jobs.
          </DisplayHeading>
        </Reveal>
        <Reveal delay={100} className="mt-12">
          <Capabilities />
        </Reveal>
      </Section>

      {/* the guardrail */}
      <Section className="border-t border-border bg-surface" inner="py-16 lg:py-20">
        <Reveal>
          <div className="flex flex-col gap-6 rounded-2xl border border-border bg-background p-8 md:flex-row md:items-center md:gap-10 lg:p-10">
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-accent text-white">
              <ShieldCheck className="h-6 w-6" strokeWidth={2} />
            </span>
            <div>
              <h3 className="text-lg font-semibold text-text-primary">
                Every capability works inside one rule
              </h3>
              <p className="mt-2 max-w-2xl text-[15px] leading-[1.7] text-text-secondary">
                AI recommends or drafts. A human approves anything consequential. Automation
                executes only after approval. The one action that resubmits a claim to a payer has
                no automated path at all.
              </p>
            </div>
            <Link to="/how-it-works" className={`shrink-0 md:ml-auto ${btnOutline}`}>
              How It Works
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </Reveal>
      </Section>

      {/* deep dive per capability */}
      <Section inner="py-20 lg:py-24">
        <Reveal>
          <Eyebrow>In detail</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 max-w-2xl text-3xl sm:text-4xl">
            How each agent works.
          </DisplayHeading>
        </Reveal>
        <Reveal delay={80} className="mt-10">
          <Capability
            index="01"
            status="Live"
            title="Claims risk detection"
            what="Before a claim is submitted, a deterministic rule engine scores it for the problems that cause denials — missing prior authorization, missing documentation, coding that doesn't match the record, overdue payer follow-up — and assigns a Low / Medium / High risk level."
            how={[
              "A reasoning agent explains each finding in plain language, grounded strictly in what the rule engine found",
              "A recommendation agent proposes the single next action and a confidence level",
              "Low-confidence or compound problems route to a person, not a one-click action",
              "The action that resubmits a claim to a payer is never automated — it always goes to a human",
            ]}
          />
          <Capability
            index="02"
            status="Live"
            title="Insurance eligibility verification"
            what="For a scheduled visit, Foresight checks the patient's coverage ahead of time so the front desk knows before the patient arrives. For an emergency or walk-in patient, the check runs in parallel with care — it never blocks, delays, or gates treatment."
            how={[
              "Coverage status per appointment: active, inactive, needs more info, or check failed",
              "\"Needs more info\" on an unidentified patient is an expected outcome, re-checked later",
              "Results feed the front desk, not a gate — the clinic decides what to do about coverage",
              "Emergency verification can never sit on a care path — that's structural, not a setting",
            ]}
          />
          <Capability
            index="03"
            status="In build"
            title="Front-desk automation"
            what="The routine, repetitive work around a visit — scheduling, reminders, intake, and status updates to the patient — handled automatically, with staff looped in on exceptions."
            how={[
              "Appointment reminders and confirmations sent on the clinic's behalf",
              "Intake and registration data collected before the visit",
              "A task queue that surfaces only what needs a human",
              "Exceptions escalate with full context, never a bare alert",
            ]}
          />
          <Capability
            index="04"
            status="Live"
            title="Human approval controls"
            what="A single decision engine — the Commander — sits between every AI suggestion and every action. It is a pure rule table with one structural guarantee: nothing consequential executes without a recorded human approval."
            how={[
              "Every claim moves through an explicit 'awaiting approval' state before anything acts",
              "Approvals are recorded against the person who made them, in an append-only log",
              "Errors and unrecognized states escalate to a person — the system never guesses",
              "The rule table is fixed and testable — same situation, same decision, every time",
            ]}
          />
        </Reveal>

        <CtaPanel
          title="See it against your own workflow."
          body="A short walkthrough with your claims, eligibility, and front-desk process — no slides."
        >
          <button onClick={() => open("product_cta")} className={btnDark}>
            Book a Demo
            <ArrowRight className="h-4 w-4" />
          </button>
          <Link to="/how-it-works" className={btnOutline}>
            How It Works
          </Link>
        </CtaPanel>
      </Section>

      <Section className="border-t border-border bg-surface" inner="py-20 lg:py-24">
        <div className="grid gap-6 sm:grid-cols-2 sm:gap-8 lg:grid-cols-4">
          <FeatureCard icon={FileSearch} title="Catches risk early">
            Problems are found before submission, when they're still cheap to fix.
          </FeatureCard>
          <FeatureCard icon={ShieldCheck} title="Coverage, before the visit">
            Eligibility is verified ahead of time — and never in the way of care.
          </FeatureCard>
          <FeatureCard icon={CalendarClock} title="Less busywork">
            Reminders, intake, and status updates handled automatically.
          </FeatureCard>
          <FeatureCard icon={UserCheck} title="People stay in charge">
            AI drafts and recommends. A human approves anything that matters.
          </FeatureCard>
        </div>
      </Section>
    </>
  );
}
