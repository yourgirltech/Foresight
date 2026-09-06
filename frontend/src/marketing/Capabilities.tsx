import { useState } from "react";
import {
  CalendarClock,
  FileSearch,
  ShieldCheck,
  UserCheck,
  type LucideIcon,
} from "lucide-react";

import { DisplayHeading } from "./components";
import {
  ApprovalVisual,
  ClaimsRiskVisual,
  EligibilityVisual,
  FrontDeskVisual,
} from "./showcase";

interface Capability {
  key: string;
  tab: string;
  icon: LucideIcon;
  title: string;
  body: string;
  points: string[];
  visual: React.ReactNode;
  status: "Live" | "In build";
}

export const CAPABILITIES: Capability[] = [
  {
    key: "claims",
    tab: "Claims risk",
    icon: FileSearch,
    status: "Live",
    title: "Catch denials before they happen",
    body: "A deterministic rule engine scores every claim for the problems that cause denials, then an agent explains each one in plain language and recommends the single next action.",
    points: [
      "Missing prior-auth, missing documentation, coding mismatches, overdue follow-up",
      "Low-confidence or compound cases route to a person, not a one-click action",
    ],
    visual: <ClaimsRiskVisual />,
  },
  {
    key: "eligibility",
    tab: "Eligibility",
    icon: ShieldCheck,
    status: "Live",
    title: "Know coverage before the patient arrives",
    body: "Verification runs ahead of a scheduled visit so the front desk isn't surprised. For an emergency patient it runs alongside care and can never gate treatment.",
    points: [
      "Active, inactive, needs-more-info, or check-failed — per appointment",
      "\"Needs more info\" is an expected outcome for a walk-in, re-checked later",
    ],
    visual: <EligibilityVisual />,
  },
  {
    key: "frontdesk",
    tab: "Front desk",
    icon: CalendarClock,
    status: "In build",
    title: "Take the busywork off the front desk",
    body: "Scheduling, reminders, intake, and patient status updates handled automatically — with staff looped in only on the exceptions that need a decision.",
    points: [
      "Reminders and confirmations sent on the clinic's behalf",
      "A task queue that surfaces only what needs a human",
    ],
    visual: <FrontDeskVisual />,
  },
  {
    key: "approval",
    tab: "Human approval",
    icon: UserCheck,
    status: "Live",
    title: "A person is always in charge of what matters",
    body: "One decision engine sits between every AI suggestion and every action. Nothing consequential executes without a recorded human approval — it isn't a setting you can turn off.",
    points: [
      "Every claim passes through an explicit 'awaiting approval' state",
      "Approvals are recorded against the person who made them",
    ],
    visual: <ApprovalVisual />,
  },
];

/** Interactive tabbed capability showcase. Reused on the landing + product pages. */
export function Capabilities() {
  const [active, setActive] = useState(0);
  const cap = CAPABILITIES[active];

  return (
    <div className="grid gap-10 lg:grid-cols-2 lg:items-center lg:gap-16">
      <div>
        <div className="flex flex-wrap gap-2">
          {CAPABILITIES.map((c, i) => (
            <button
              key={c.key}
              onClick={() => setActive(i)}
              className={`inline-flex items-center gap-2 rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                i === active
                  ? "border-accent bg-accent-soft text-accent"
                  : "border-border bg-surface text-text-secondary hover:bg-surface-muted"
              }`}
            >
              <c.icon className="h-4 w-4" strokeWidth={2} />
              {c.tab}
            </button>
          ))}
        </div>

        <div key={cap.key} className="mt-8 animate-[fp-fade-up_0.4s_ease] motion-reduce:animate-none">
          <div className="flex items-center gap-3">
            <DisplayHeading as="h3" className="text-2xl sm:text-[1.75rem]">
              {cap.title}
            </DisplayHeading>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                cap.status === "Live" ? "bg-success-soft text-success" : "bg-accent-soft text-accent"
              }`}
            >
              {cap.status}
            </span>
          </div>
          <p className="mt-3 max-w-lg text-[15px] leading-[1.75] text-text-secondary">{cap.body}</p>
          <ul className="mt-5 space-y-2.5">
            {cap.points.map((p) => (
              <li key={p} className="flex gap-2.5 text-sm leading-[1.6] text-text-secondary">
                <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
                {p}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="relative">
        <div key={cap.key} className="animate-[fp-fade-up_0.45s_ease] motion-reduce:animate-none">
          {cap.visual}
        </div>
      </div>
    </div>
  );
}
