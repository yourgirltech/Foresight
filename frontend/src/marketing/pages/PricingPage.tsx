import { Link } from "react-router-dom";
import { ArrowRight, Building2, Check, SlidersHorizontal, Users2 } from "lucide-react";

import { useDemoModal } from "../DemoModal";
import { Reveal } from "../motion";
import {
  CtaPanel,
  DisplayHeading,
  Eyebrow,
  PageHeader,
  Section,
  btnDark,
  btnOutline,
} from "../components";

// Pricing is being finalized. Honest about that; describes the model we're
// building toward — NOT a fake price sheet.
const FACTORS = [
  {
    icon: Users2,
    head: "Size of your organization",
    body: "Number of providers and claim volume. A five-provider clinic and a hospital department shouldn't pay the same.",
  },
  {
    icon: SlidersHorizontal,
    head: "Which capabilities you turn on",
    body: "Claims-risk detection, eligibility verification, and front-desk automation can be adopted one at a time.",
  },
  {
    icon: Building2,
    head: "Payer complexity",
    body: "How many payers you bill and how involved their rules are affects the setup work, not the ongoing price.",
  },
];

const INCLUDED = [
  "Unlimited users on your team — approvals are never metered",
  "The Commander and every agent your plan includes",
  "The full activity log and audit trail",
  "Onboarding support to configure your payer rules",
  "Tenant isolation at the database level, by default",
  "Every future agent that lands in your plan's scope",
];

export function PricingPage() {
  const { open } = useDemoModal();
  return (
    <>
      <PageHeader
        eyebrow="Pricing"
        title="Straightforward pricing, matched to your organization."
        intro="We're finalizing packaging and will publish plans here. In the meantime, pricing is set per organization based on a few simple factors — and every plan is built so that keeping a human in the loop is never something you pay extra for."
        chips={["Per organization", "No approval metering", "Adopt module by module"]}
      />

      <Section inner="py-20 lg:py-24">
        <Reveal>
          <Eyebrow>What sets the price</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 max-w-xl text-3xl sm:text-4xl">
            Three factors, nothing hidden.
          </DisplayHeading>
        </Reveal>
        <div className="mt-12 grid gap-6 sm:gap-8 lg:grid-cols-3">
          {FACTORS.map((f, i) => (
            <Reveal key={f.head} delay={i * 70}>
              <div className="h-full rounded-2xl border border-border bg-surface p-7 lg:p-8">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-accent">
                  <f.icon className="h-5 w-5" strokeWidth={2} />
                </span>
                <p className="mt-4 text-sm font-semibold text-text-primary">{f.head}</p>
                <p className="mt-2.5 text-sm leading-[1.7] text-text-secondary">{f.body}</p>
              </div>
            </Reveal>
          ))}
        </div>

        {/* included */}
        <Reveal delay={80}>
          <div className="mt-12 overflow-hidden rounded-3xl border border-border">
            <div className="border-b border-border bg-surface px-8 py-6 lg:px-10">
              <h3 className="font-display text-xl font-semibold text-text-primary">
                Every plan includes
              </h3>
            </div>
            <ul className="grid gap-x-10 gap-y-4 bg-background px-8 py-8 sm:grid-cols-2 lg:px-10">
              {INCLUDED.map((i) => (
                <li key={i} className="flex gap-3 text-sm leading-[1.6] text-text-secondary">
                  <span className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-success-soft text-success">
                    <Check className="h-3 w-3" strokeWidth={3} />
                  </span>
                  {i}
                </li>
              ))}
            </ul>
          </div>
        </Reveal>

        <CtaPanel
          title="Get a quote for your organization"
          body="Tell us your size and what you want to improve, and we'll put pricing together on the demo call — no obligation."
        >
          <button onClick={() => open("pricing_cta")} className={btnDark}>
            Book a Demo
            <ArrowRight className="h-4 w-4" />
          </button>
          <Link to="/signup" className={btnOutline}>
            Get Started Free
          </Link>
        </CtaPanel>
      </Section>
    </>
  );
}
