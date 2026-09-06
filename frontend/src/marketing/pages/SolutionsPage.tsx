import { Link } from "react-router-dom";
import { ArrowRight, Building2, Layers, Stethoscope } from "lucide-react";

import { useDemoModal } from "../DemoModal";
import { Reveal } from "../motion";
import { EligibilityVisual, FrontDeskVisual } from "../showcase";
import {
  CtaPanel,
  DisplayHeading,
  MiniStat,
  PageHeader,
  Section,
  btnDark,
  btnOutline,
} from "../components";

interface AudienceProps {
  id: string;
  icon: typeof Building2;
  label: string;
  title: string;
  summary: string;
  stats: { value: string; label: string }[];
  points: { head: string; body: string }[];
  visual: React.ReactNode;
  flip?: boolean;
}

function Audience({
  id,
  icon: Icon,
  label,
  title,
  summary,
  stats,
  points,
  visual,
  flip,
}: AudienceProps) {
  const { open } = useDemoModal();
  return (
    <div id={id} className="scroll-mt-28">
      <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
        <div className={flip ? "lg:order-2" : ""}>
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent-soft text-accent">
              <Icon className="h-5 w-5" strokeWidth={2} />
            </span>
            <span className="text-xs font-semibold uppercase tracking-[0.18em] text-accent/80">
              {label}
            </span>
          </div>
          <DisplayHeading as="h2" className="mt-5 text-3xl sm:text-[2rem]">
            {title}
          </DisplayHeading>
          <p className="mt-4 max-w-xl text-[15px] leading-[1.75] text-text-secondary">{summary}</p>

          <div className="mt-8 grid grid-cols-3 gap-6 border-y border-border py-6">
            {stats.map((s) => (
              <MiniStat key={s.label} value={s.value} label={s.label} />
            ))}
          </div>

          <ul className="mt-8 space-y-4">
            {points.map((p) => (
              <li key={p.head}>
                <p className="text-sm font-semibold text-text-primary">{p.head}</p>
                <p className="mt-1 text-sm leading-[1.65] text-text-secondary">{p.body}</p>
              </li>
            ))}
          </ul>

          <button onClick={() => open(`solutions_${id}`)} className={`mt-9 ${btnDark}`}>
            Book a Demo
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>

        <div className={`relative ${flip ? "lg:order-1" : ""}`}>
          <div className="pointer-events-none absolute -inset-6 rounded-3xl bg-accent/[0.06] blur-2xl" />
          <div className="relative">{visual}</div>
        </div>
      </div>
    </div>
  );
}

export function SolutionsPage() {
  const { open } = useDemoModal();
  return (
    <>
      <PageHeader
        eyebrow="Solutions"
        title="Built for the way your organization actually runs."
        intro="The same agents, tuned to the pressures you face. Independent practices need denials down and staff time back. Hospital departments need coverage confirmed and exceptions surfaced without adding headcount."
        chips={["For Clinics", "For Hospitals", "Health Systems"]}
      />

      <Section inner="py-20 lg:py-28">
        <div className="space-y-24 lg:space-y-32">
          <Reveal>
            <Audience
              id="clinics"
              icon={Stethoscope}
              label="For Clinics"
              title="A small team that finally isn't drowning in follow-up."
              summary="Independent and group practices where everyone wears several hats. Foresight takes the repetitive revenue-cycle and front-desk work off their plate and flags only the handful of things that genuinely need a decision."
              stats={[
                { value: "Before", label: "risk caught pre-submission" },
                { value: "Auto", label: "reminders & intake" },
                { value: "0", label: "new hires needed" },
              ]}
              points={[
                {
                  head: "Fewer denials, less rework",
                  body: "Risk is caught before submission, so staff aren't chasing appeals weeks later.",
                },
                {
                  head: "A lighter front desk",
                  body: "Eligibility checked ahead of time; reminders and intake handled automatically.",
                },
                {
                  head: "The AI does the drafting",
                  body: "Your team reviews the exceptions, approves, and moves on with their day.",
                },
              ]}
              visual={<FrontDeskVisual />}
            />
          </Reveal>

          <Reveal>
            <Audience
              id="hospitals"
              icon={Building2}
              label="For Hospitals"
              title="Scale the review without scaling the team."
              summary="Departments and health systems with higher volume, more payers, and stricter compliance. Foresight scores and explains every claim automatically, surfaces exceptions with full context, and keeps a clean record of every decision."
              stats={[
                { value: "Every", label: "claim scored & explained" },
                { value: "Parallel", label: "emergency eligibility" },
                { value: "Full", label: "audit trail" },
              ]}
              points={[
                {
                  head: "Volume without backlog",
                  body: "Every claim is scored and explained automatically; people review the exceptions.",
                },
                {
                  head: "Emergency-safe by design",
                  body: "Eligibility for an unscheduled patient runs alongside care and can never gate treatment.",
                },
                {
                  head: "Audit-ready by default",
                  body: "An append-only activity log records every AI action and every human approval.",
                },
              ]}
              visual={<EligibilityVisual />}
              flip
            />
          </Reveal>
        </div>
      </Section>

      {/* shared foundation */}
      <Section className="border-t border-border bg-surface" inner="py-16 lg:py-20">
        <Reveal>
          <div className="flex flex-col gap-6 rounded-2xl border border-border bg-background p-8 md:flex-row md:items-center md:gap-10 lg:p-10">
            <span className="grid h-12 w-12 shrink-0 place-items-center rounded-xl bg-pro text-white">
              <Layers className="h-6 w-6" strokeWidth={2} />
            </span>
            <div>
              <h3 className="text-lg font-semibold text-text-primary">One foundation underneath both</h3>
              <p className="mt-2 max-w-2xl text-[15px] leading-[1.7] text-text-secondary">
                Every organization is a separate tenant, isolated at the database level. The same
                Commander, the same agents, the same human-approval rule — sized to you, never
                shared with anyone else.
              </p>
            </div>
            <Link to="/how-it-works" className={`shrink-0 md:ml-auto ${btnOutline}`}>
              How It Works
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </Reveal>

        <CtaPanel title="Not sure which fits? Let's talk it through.">
          <button onClick={() => open("solutions_cta")} className={btnDark}>
            Book a Demo
            <ArrowRight className="h-4 w-4" />
          </button>
          <Link to="/product" className={btnOutline}>
            See the product
          </Link>
        </CtaPanel>
      </Section>
    </>
  );
}
