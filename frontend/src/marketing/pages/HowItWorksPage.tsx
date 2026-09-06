import { Link } from "react-router-dom";
import {
  ArrowRight,
  Gauge,
  HeartPulse,
  ShieldCheck,
  Split,
} from "lucide-react";

import { useDemoModal } from "../DemoModal";
import { Reveal } from "../motion";
import { CommanderFlowVisual } from "../showcase";
import {
  CtaPanel,
  DisplayHeading,
  Eyebrow,
  PageHeader,
  Section,
  StepMarker,
  btnDark,
  btnOutline,
} from "../components";

const STEPS = [
  {
    title: "Something happens in your day",
    body: "A claim is ready to go out. An appointment gets booked. A patient walks into the ER. Foresight notices each of these the moment it happens.",
  },
  {
    title: "The Commander decides who should look at it",
    body: "The Commander is a simple, predictable rulebook — not a black box. For each event it decides one thing: which specialist handles this next, or whether a person needs to step in. It never takes an action itself.",
  },
  {
    title: "A specialist agent does one focused job",
    body: "One agent scores a claim for denial risk. Another explains what it found, in plain language. Another checks a patient's insurance. Each is narrow and good at its task, and hands its work back to the Commander when done.",
  },
  {
    title: "A person approves anything that matters",
    body: "Before Foresight does anything with real consequences — resubmitting a claim, sending something to a payer — it stops and puts the recommendation in front of a member of your team. Nothing consequential happens without that approval, and every approval is recorded.",
  },
  {
    title: "The routine part gets handled",
    body: "Once a person has approved, the low-stakes follow-through — filing the request, setting the reminder, logging the record — is carried out automatically, and written to an activity log you can review any time.",
  },
];

const PRINCIPLES = [
  {
    icon: ShieldCheck,
    head: "AI recommends. A human approves. Automation executes only after approval.",
    body: "This is the rule the whole system is built around. It isn't a setting you can turn off.",
  },
  {
    icon: Gauge,
    head: "The decision engine is predictable",
    body: "The Commander follows a fixed, ordered set of rules. Given the same situation, it makes the same call every time — and the rules are written down.",
  },
  {
    icon: HeartPulse,
    head: "Emergency care is never gated",
    body: "When a patient needs care now, insurance checks run alongside treatment and can't delay it. Enforced by how the system is wired, not left to good intentions.",
  },
  {
    icon: Split,
    head: "When in doubt, it asks a person",
    body: "Low confidence, an error, or a situation the rules don't recognize all route to a human. The system does not guess.",
  },
];

const ROSTER = [
  { id: "00", name: "Commander", job: "Routes every event. Pure rule table — takes no action itself." },
  { id: "01", name: "Eligibility", job: "Verifies coverage. Runs alongside care in an emergency." },
  { id: "06", name: "Analyzer", job: "Deterministic risk engine — finds the issues on a claim." },
  { id: "07", name: "Reasoning", job: "Explains the findings in plain language, strictly grounded." },
  { id: "08", name: "Recommendation", job: "Turns the issue list into one action + a confidence level." },
  { id: "09 / 10", name: "Executors", job: "Carry out an approved follow-up or reminder — after sign-off." },
  { id: "12", name: "Escalation", job: "The safety net. Logs full context and hands a claim to a person." },
];

export function HowItWorksPage() {
  const { open } = useDemoModal();
  return (
    <>
      <PageHeader
        eyebrow="How It Works"
        title="One coordinator, a team of specialists, and your staff in charge."
        intro="You don't need to understand AI to understand Foresight. Think of it as a well-run back office: a coordinator who routes work, specialists who each do one job, and your team signing off on the decisions that count."
        chips={["Predictable", "Human-in-the-loop", "Emergency-safe"]}
        visual={
          <div className="fp-float-slow">
            <CommanderFlowVisual />
          </div>
        }
      />

      {/* the walkthrough — vertical timeline */}
      <Section inner="py-20 lg:py-28">
        <Reveal>
          <Eyebrow>Step by step</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 max-w-xl text-3xl sm:text-4xl">
            From an event to a handled outcome.
          </DisplayHeading>
        </Reveal>

        <div className="relative mt-14">
          <div className="absolute left-[17px] top-2 bottom-2 w-px bg-border md:left-[19px]" />
          <div className="space-y-10">
            {STEPS.map((s, i) => (
              <Reveal key={s.title} delay={i * 80}>
                <div className="relative flex gap-6 md:gap-8">
                  <div className="relative z-10">
                    <StepMarker>{i + 1}</StepMarker>
                  </div>
                  <div className="min-w-0 pb-2 pt-1">
                    <h3 className="text-lg font-semibold text-text-primary">{s.title}</h3>
                    <p className="mt-2 max-w-2xl text-[15px] leading-[1.75] text-text-secondary">
                      {s.body}
                    </p>
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </Section>

      {/* the agent roster */}
      <Section className="border-t border-border bg-surface" inner="py-20 lg:py-24">
        <Reveal>
          <Eyebrow>The team</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 max-w-xl text-3xl sm:text-4xl">
            Meet the agents.
          </DisplayHeading>
          <p className="mt-4 max-w-xl text-[15px] leading-[1.7] text-text-secondary">
            Each is deliberately narrow. Numbers 02–05 and 11 are reserved for later phases.
          </p>
        </Reveal>
        <Reveal delay={100} className="mt-12">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {ROSTER.map((a) => (
              <div
                key={a.id}
                className="rounded-2xl border border-border bg-background p-5 transition-colors hover:border-border-strong"
              >
                <div className="flex items-baseline gap-2.5">
                  <span className="font-display text-lg font-semibold text-accent">{a.id}</span>
                  <span className="text-sm font-semibold text-text-primary">{a.name}</span>
                </div>
                <p className="mt-2 text-sm leading-[1.6] text-text-secondary">{a.job}</p>
              </div>
            ))}
          </div>
        </Reveal>
      </Section>

      {/* rules that don't bend */}
      <Section inner="py-20 lg:py-24">
        <Reveal>
          <Eyebrow>Non-negotiable</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 text-3xl sm:text-4xl">
            The rules that don't bend.
          </DisplayHeading>
        </Reveal>
        <div className="mt-12 grid gap-6 sm:grid-cols-2">
          {PRINCIPLES.map((p, i) => (
            <Reveal key={p.head} delay={i * 70}>
              <div className="h-full rounded-2xl border border-border bg-surface p-7">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-accent">
                  <p.icon className="h-5 w-5" strokeWidth={2} />
                </span>
                <p className="mt-4 text-sm font-semibold text-text-primary">{p.head}</p>
                <p className="mt-2.5 text-sm leading-[1.7] text-text-secondary">{p.body}</p>
              </div>
            </Reveal>
          ))}
        </div>

        <CtaPanel title="Want to see it run on your process?">
          <button onClick={() => open("how_it_works_cta")} className={btnDark}>
            Book a Demo
            <ArrowRight className="h-4 w-4" />
          </button>
          <Link to="/product" className={btnOutline}>
            See the capabilities
          </Link>
        </CtaPanel>
      </Section>
    </>
  );
}
