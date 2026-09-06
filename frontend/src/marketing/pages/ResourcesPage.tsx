import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpRight, BookOpen, FileText, Newspaper, Video } from "lucide-react";

import { useDemoModal } from "../DemoModal";
import { Reveal } from "../motion";
import { CtaPanel, DisplayHeading, Eyebrow, PageHeader, Section, btnDark } from "../components";

// Real, planned sections — the copy describes what will live here. Deliberately
// not filled with sample articles or fake case studies.
const SECTIONS = [
  {
    id: "guides",
    icon: BookOpen,
    title: "Guides",
    body: "Practical playbooks for revenue-cycle and front-desk teams — how to cut denial rates, structure prior-auth workflows, and introduce AI review without disrupting staff. Written for practitioners, not vendors.",
  },
  {
    id: "docs",
    icon: FileText,
    title: "Documentation",
    body: "How Foresight's agents work, what each one checks, how the approval controls behave, and how to configure payer rules for your organization. Published as the product stabilizes.",
  },
  {
    id: "updates",
    icon: Newspaper,
    title: "Product updates",
    body: "A running log of what's shipped and what's next — new agents, expanded payer coverage, and the front-desk automation currently in build.",
  },
  {
    id: "webinars",
    icon: Video,
    title: "Webinars & walkthroughs",
    body: "Recorded sessions on claims-risk detection, eligibility verification, and how the Commander keeps a human in control of consequential actions.",
  },
];

export function ResourcesPage() {
  const { open } = useDemoModal();
  return (
    <>
      <PageHeader
        eyebrow="Resources"
        title="Learn how proactive revenue-cycle operations actually work."
        intro="We're building this library alongside the product. Below is what will live here — no filler, no fake case studies. If there's something you need now, a demo is the fastest way to get answers."
        chips={["Guides", "Docs", "Product updates", "Webinars"]}
      />

      {/* featured slot */}
      <Section inner="py-20 lg:py-24">
        <Reveal>
          <Link
            to="/how-it-works"
            className="group grid gap-8 overflow-hidden rounded-3xl border border-border bg-surface p-8 transition-all hover:border-border-strong hover:shadow-[0_24px_60px_-28px_rgb(var(--color-text-primary)/0.25)] md:grid-cols-[1.2fr_1fr] md:items-center lg:p-12"
          >
            <div>
              <span className="rounded-full bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">
                Start here
              </span>
              <DisplayHeading as="h2" className="mt-5 text-2xl sm:text-3xl">
                How the Commander keeps a human in charge
              </DisplayHeading>
              <p className="mt-4 max-w-lg text-[15px] leading-[1.75] text-text-secondary">
                A plain-language walkthrough of the decision engine and the agents it coordinates —
                written for a healthcare administrator, not an engineer.
              </p>
              <span className="mt-6 inline-flex items-center gap-1.5 text-sm font-semibold text-accent">
                Read the walkthrough
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </span>
            </div>
            <div className="relative hidden md:block">
              <div className="fp-dotgrid absolute inset-0 rounded-2xl opacity-60" />
              <div className="relative rounded-2xl border border-border bg-background p-6">
                <ol className="space-y-3 text-sm">
                  {["Event", "Commander routes", "Specialist agent", "Human approves", "Automation runs"].map(
                    (t, i) => (
                      <li key={t} className="flex items-center gap-3 text-text-secondary">
                        <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md bg-accent-soft text-xs font-bold text-accent">
                          {i + 1}
                        </span>
                        {t}
                      </li>
                    ),
                  )}
                </ol>
              </div>
            </div>
          </Link>
        </Reveal>

        <Reveal delay={100} className="mt-16">
          <Eyebrow>The library</Eyebrow>
          <DisplayHeading as="h2" className="mt-4 text-3xl sm:text-4xl">
            What's coming.
          </DisplayHeading>
        </Reveal>
        <div className="mt-10 grid gap-6 sm:grid-cols-2 sm:gap-8">
          {SECTIONS.map((s, i) => (
            <Reveal key={s.id} delay={i * 70}>
              <div
                id={s.id}
                className="h-full scroll-mt-28 rounded-2xl border border-border bg-surface p-7 lg:p-8"
              >
                <div className="flex items-center gap-3">
                  <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-accent">
                    <s.icon className="h-5 w-5" strokeWidth={2} />
                  </span>
                  <h3 className="text-base font-semibold text-text-primary">{s.title}</h3>
                  <span className="ml-auto rounded-full bg-surface-muted px-2 py-0.5 text-xs font-medium text-text-secondary">
                    Coming soon
                  </span>
                </div>
                <p className="mt-4 text-sm leading-[1.7] text-text-secondary">{s.body}</p>
              </div>
            </Reveal>
          ))}
        </div>

        <CtaPanel
          title="Have a question now?"
          body="Bring it to a demo. We'll walk through exactly how Foresight would handle your claims, eligibility, and front-desk workflows."
        >
          <button onClick={() => open("resources_cta")} className={btnDark}>
            Book a Demo
            <ArrowRight className="h-4 w-4" />
          </button>
          <Link
            to="/how-it-works"
            className="inline-flex items-center gap-1.5 text-sm font-semibold text-text-secondary transition-colors hover:text-text-primary"
          >
            Read How It Works
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </CtaPanel>
      </Section>
    </>
  );
}
