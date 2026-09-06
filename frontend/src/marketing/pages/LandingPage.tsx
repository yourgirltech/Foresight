import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  Building2,
  CheckCircle2,
  ChevronDown,
  Clock,
  FileSearch,
  HeartPulse,
  PlayCircle,
  ShieldCheck,
  Stethoscope,
  UserCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

import { Capabilities } from "../Capabilities";
import { DashboardPreview } from "../DashboardPreview";
import { useDemoModal } from "../DemoModal";
import { DisplayHeading, Eyebrow, Section, btnDark, btnGhost, btnOutline } from "../components";
import { CountUp, Marquee, Reveal } from "../motion";
import { FlowArrow } from "../showcase";

const BENEFITS = [
  { icon: BarChart3, label: "Reduce denials" },
  { icon: Clock, label: "Save hours every week" },
  { icon: Users, label: "Empower your team" },
];

// Illustrative PLACEHOLDER organization names — not real Foresight clients.
const PLACEHOLDER_ORGS = [
  { name: "Riverside Family Medicine", icon: Stethoscope },
  { name: "Lakeside Pediatrics", icon: HeartPulse },
  { name: "Northgate Health System", icon: Building2 },
  { name: "Summit Community Clinic", icon: Stethoscope },
  { name: "Cedar Valley Hospital", icon: Building2 },
  { name: "Harbor Point Medical Group", icon: HeartPulse },
];

function FloatingPill({ className, children }: { className: string; children: React.ReactNode }) {
  return (
    <div
      className={`absolute z-10 flex items-center gap-2 whitespace-nowrap rounded-full border border-border bg-surface px-3.5 py-2 text-xs font-medium text-text-primary shadow-lg ${className}`}
    >
      <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
      {children}
    </div>
  );
}

/* ================================================================== *
 * stats
 * ================================================================== */
function Stat({
  value,
  label,
  render,
}: {
  value: number;
  label: string;
  render: (n: React.ReactNode) => React.ReactNode;
}) {
  return (
    <div className="text-center">
      <p className="font-display text-4xl font-semibold tracking-tight text-text-primary sm:text-5xl">
        {render(<CountUp to={value} />)}
      </p>
      <p className="mt-2 text-sm text-text-secondary">{label}</p>
    </div>
  );
}

/* ================================================================== *
 * how it works
 * ================================================================== */
const STEPS: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: FileSearch,
    title: "The AI reviews",
    body: "Specialist agents score risk, verify coverage, and draft the next step — each doing one focused job.",
  },
  {
    icon: UserCheck,
    title: "A person approves",
    body: "Anything consequential stops and waits for a member of your team. Every approval is recorded.",
  },
  {
    icon: CheckCircle2,
    title: "Automation executes",
    body: "Only after sign-off does the routine follow-through happen — filed, logged, and reviewable.",
  },
];

/* ================================================================== *
 * FAQ
 * ================================================================== */
const FAQS: { q: string; a: string }[] = [
  {
    q: "Does the AI ever act on its own?",
    a: "No. Foresight's decision engine — the Commander — routes work to specialist agents, but nothing with real consequences (resubmitting a claim, sending something to a payer) executes without a recorded human approval. It's enforced by how the system is built, not a preference.",
  },
  {
    q: "Will it slow down emergency care?",
    a: "Never. For a scheduled visit, eligibility is verified ahead of time. For an emergency or walk-in patient, the check runs in parallel with care and can't block, delay, or gate treatment — that's structural.",
  },
  {
    q: "How is it different from a clearinghouse or a rules engine we already have?",
    a: "Those tell you a claim was rejected. Foresight scores the risk before submission, explains why in plain language, recommends the fix, and — once a person approves — carries out the low-stakes follow-through automatically.",
  },
  {
    q: "What does it take to get started?",
    a: "Sign up, connect your payer rules, and the claims-risk and eligibility agents can start reviewing right away. Onboarding support helps configure the payer-specific rules for your organization.",
  },
  {
    q: "Is our data isolated from other organizations?",
    a: "Yes. Every clinic and hospital is a separate tenant, isolated at the database level. One organization can never see another's data.",
  },
];

function FaqItem({ q, a }: { q: string; a: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-border">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-6 py-5 text-left"
        aria-expanded={open}
      >
        <span className="text-[15px] font-medium text-text-primary">{q}</span>
        <ChevronDown
          className={`h-5 w-5 shrink-0 text-text-muted transition-transform duration-300 ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>
      <div
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: open ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          <p className="pb-5 pr-10 text-sm leading-[1.75] text-text-secondary">{a}</p>
        </div>
      </div>
    </div>
  );
}

/* ================================================================== *
 * page
 * ================================================================== */
export function LandingPage() {
  const { open } = useDemoModal();

  return (
    <>
      {/* ============================ hero ============================ */}
      <div className="hero-photo-bg relative overflow-hidden">
        <Section inner="py-20 lg:py-24">
          <div className="grid items-center gap-14 lg:grid-cols-2 lg:gap-16">
            {/* left — copy */}
            <div className="min-w-0">
              <Reveal>
                <Eyebrow>AI for modern healthcare organizations</Eyebrow>
              </Reveal>
              <Reveal delay={80}>
                <DisplayHeading
                  as="h1"
                  className="mt-6 max-w-[14ch] text-[2.5rem] leading-[1.1] sm:text-[3.25rem]"
                >
                  A smarter, more profitable <span className="text-accent">healthcare</span> system.
                </DisplayHeading>
              </Reveal>
              <Reveal delay={160}>
                <p className="mt-7 max-w-md text-lg leading-[1.8] text-text-secondary">
                  Foresight uses specialized AI agents to prevent insurance losses, automate
                  front-desk operations, and keep your entire clinic or hospital running smoothly —
                  with humans always in control of what matters.
                </p>
              </Reveal>
              <Reveal delay={240}>
                <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-4">
                  <Link to="/signup" className={btnDark}>
                    Get Started Free
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                  <button onClick={() => open("landing_hero")} className={btnOutline}>
                    Book a Demo
                  </button>
                  <Link to="/how-it-works" className={btnGhost}>
                    <PlayCircle className="h-4 w-4" />
                    See How It Works
                  </Link>
                </div>
              </Reveal>
            </div>

            {/* right — a non-interactive re-render of the REAL Dashboard */}
            <Reveal delay={200}>
              <div className="relative mx-auto w-fit min-w-0 max-w-full pb-4 pt-11 lg:mx-0">
                <div className="fp-float-slow relative h-[300px] w-[392px] max-w-full overflow-hidden rounded-2xl border border-border-strong bg-surface shadow-[0_28px_80px_-24px_rgba(15,23,42,0.4)]">
                  <div
                    style={{ width: 1180, transform: "scale(0.332)", transformOrigin: "top left" }}
                  >
                    <DashboardPreview width={1180} />
                  </div>
                  <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-background to-transparent" />
                </div>
                <FloatingPill className="fp-float left-0 top-1">
                  Insurance verified before the visit
                </FloatingPill>
              </div>
            </Reveal>
          </div>

          {/* benefits — full-width row below the CTAs, set apart by a divider */}
          <Reveal delay={120}>
            <div className="mt-14 border-t border-border/70 pt-12 lg:mt-16">
              <div className="grid gap-10 sm:grid-cols-3 sm:gap-8">
                {BENEFITS.map((b) => (
                  <div key={b.label} className="flex items-center gap-3.5">
                    <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
                      <b.icon className="h-5 w-5" strokeWidth={2} />
                    </span>
                    <span className="text-[15px] font-medium text-text-primary">{b.label}</span>
                  </div>
                ))}
              </div>
            </div>
          </Reveal>
        </Section>
      </div>

      {/* ========================== trusted by ========================== */}
      <Section className="border-t border-border bg-surface" inner="py-16 lg:py-20">
        <p className="text-center text-xs font-semibold uppercase tracking-[0.2em] text-text-muted">
          Trusted by clinics and hospitals
        </p>
        <div className="mt-10">
          <Marquee>
            {PLACEHOLDER_ORGS.map((o) => (
              <div
                key={o.name}
                className="flex shrink-0 items-center gap-2.5 text-text-secondary"
              >
                <o.icon className="h-5 w-5" strokeWidth={1.75} />
                <span className="whitespace-nowrap text-sm font-semibold">{o.name}</span>
              </div>
            ))}
          </Marquee>
        </div>
        <p className="mt-8 text-center text-[11px] text-text-muted">
          Organization names shown are illustrative placeholders.
        </p>
      </Section>

      {/* ============================= stats ============================= */}
      <Section className="relative border-t border-border" inner="py-20 lg:py-24">
        <div className="fp-dotgrid pointer-events-none absolute inset-0 opacity-60" />
        <div className="relative">
          <Reveal className="text-center">
            <Eyebrow>Under the hood</Eyebrow>
            <DisplayHeading as="h2" className="mx-auto mt-4 max-w-2xl text-3xl sm:text-4xl">
              Designed to be trusted, not just fast.
            </DisplayHeading>
          </Reveal>
          <Reveal delay={100}>
            <div className="mt-14 grid grid-cols-2 gap-x-6 gap-y-12 lg:grid-cols-4">
              <Stat value={7} label="Specialist agents, one coordinator" render={(n) => n} />
              <Stat value={1} label="Human approval gate on every action" render={(n) => n} />
              <Stat
                value={0}
                label="Consequential actions without sign-off"
                render={(n) => n}
              />
              <Stat
                value={100}
                label="Of decisions written to an audit log"
                render={(n) => <>{n}%</>}
              />
            </div>
          </Reveal>
        </div>
      </Section>

      {/* ========================= capabilities ========================= */}
      <Section className="border-t border-border bg-surface" inner="py-20 lg:py-24">
        <Reveal>
          <Eyebrow>What Foresight does</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 max-w-2xl text-3xl sm:text-4xl">
            Focused agents for the healthcare revenue cycle.
          </DisplayHeading>
        </Reveal>
        <Reveal delay={100} className="mt-12">
          <Capabilities />
        </Reveal>
      </Section>

      {/* ========================= how it works ========================= */}
      <Section className="border-t border-border" inner="py-20 lg:py-24">
        <Reveal className="text-center">
          <Eyebrow>How it works</Eyebrow>
          <DisplayHeading as="h2" className="mx-auto mt-4 max-w-xl text-3xl sm:text-4xl">
            A coordinator, a team of specialists, and your staff in charge.
          </DisplayHeading>
        </Reveal>

        <div className="mt-16 flex flex-col items-stretch gap-6 md:flex-row md:items-center">
          {STEPS.map((s, i) => (
            <div key={s.title} className="contents">
              <Reveal delay={i * 120} className="flex-1">
                <div className="h-full rounded-2xl border border-border bg-surface p-7 text-center">
                  <span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-accent-soft text-accent">
                    <s.icon className="h-5 w-5" strokeWidth={2} />
                  </span>
                  <p className="mt-4 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                    Step {i + 1}
                  </p>
                  <h3 className="mt-1 text-base font-semibold text-text-primary">{s.title}</h3>
                  <p className="mx-auto mt-2 max-w-xs text-sm leading-[1.7] text-text-secondary">
                    {s.body}
                  </p>
                </div>
              </Reveal>
              {i < STEPS.length - 1 && <FlowArrow />}
            </div>
          ))}
        </div>

        <Reveal delay={120} className="mt-12 text-center">
          <Link to="/how-it-works" className={btnOutline}>
            See the full walkthrough
            <ArrowRight className="h-4 w-4" />
          </Link>
        </Reveal>
      </Section>

      {/* ==================== human-in-the-loop band ==================== */}
      <Section
        className="border-t border-border bg-surface"
        inner="py-20 text-center lg:py-24"
      >
        <Reveal>
          <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-accent text-white shadow-lg">
            <ShieldCheck className="h-7 w-7" strokeWidth={2} />
          </span>
          <DisplayHeading
            as="h2"
            className="mx-auto mt-8 max-w-3xl text-2xl leading-[1.35] sm:text-[2rem]"
          >
            &ldquo;AI recommends or drafts. A human approves anything consequential. Automation
            executes only <span className="text-accent">after</span> approval.&rdquo;
          </DisplayHeading>
          <p className="mx-auto mt-6 max-w-xl text-[15px] leading-[1.75] text-text-secondary">
            This is a system-wide rule, not a feature. Every agent Foresight adds — now and later —
            works within it.
          </p>
        </Reveal>
      </Section>

      {/* ============================== FAQ ============================== */}
      <Section className="border-t border-border" inner="py-20 lg:py-24">
        <div className="grid gap-12 lg:grid-cols-[0.9fr_1.4fr] lg:gap-20">
          <Reveal>
            <Eyebrow>Questions</Eyebrow>
            <DisplayHeading as="h2" className="mt-4 text-3xl sm:text-4xl">
              Answers, up front.
            </DisplayHeading>
            <p className="mt-4 text-sm leading-[1.75] text-text-secondary">
              Still have one? A demo is the fastest way to get it answered against your own
              workflow.
            </p>
            <button onClick={() => open("landing_faq")} className={`mt-6 ${btnOutline}`}>
              Book a Demo
              <ArrowRight className="h-4 w-4" />
            </button>
          </Reveal>
          <Reveal delay={100}>
            <div className="border-t border-border">
              {FAQS.map((f) => (
                <FaqItem key={f.q} {...f} />
              ))}
            </div>
          </Reveal>
        </div>
      </Section>

      {/* ========================= final CTA ========================= */}
      <Section className="border-t border-border" inner="py-4">
        <Reveal>
          <div
            className="relative overflow-hidden rounded-3xl border border-border px-6 py-20 text-center lg:py-28"
            style={{
              background:
                "radial-gradient(80% 120% at 50% 0%, rgb(var(--color-accent) / 0.14), transparent 60%), rgb(var(--color-surface))",
            }}
          >
            <div className="fp-dotgrid pointer-events-none absolute inset-0 opacity-40" />
            <div className="relative">
              <DisplayHeading as="h2" className="mx-auto max-w-2xl text-3xl sm:text-[2.75rem]">
                A more proactive healthcare system is possible.
              </DisplayHeading>
              <p className="mx-auto mt-5 max-w-lg text-[15px] leading-[1.75] text-text-secondary">
                Start free, or walk through it with our team against the workflow you run today.
              </p>
              <div className="mt-10 flex flex-wrap justify-center gap-4">
                <Link to="/signup" className={btnDark}>
                  Get Started Free
                  <ArrowRight className="h-4 w-4" />
                </Link>
                <button onClick={() => open("landing_footer")} className={btnOutline}>
                  Book a Demo
                </button>
              </div>
            </div>
          </div>
        </Reveal>
      </Section>

      <div className="h-12" />
    </>
  );
}
