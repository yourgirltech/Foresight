import { Link } from "react-router-dom";

import { Logo } from "./components";

const COLUMNS: { title: string; links: { label: string; to: string }[] }[] = [
  {
    title: "Product",
    links: [
      { label: "Overview", to: "/product" },
      { label: "How It Works", to: "/how-it-works" },
      { label: "Pricing", to: "/pricing" },
    ],
  },
  {
    title: "Solutions",
    links: [
      { label: "For Clinics", to: "/solutions#clinics" },
      { label: "For Hospitals", to: "/solutions#hospitals" },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "Resources", to: "/resources" },
      { label: "Log in", to: "/login" },
      { label: "Sign up", to: "/signup" },
    ],
  },
];

export function MarketingFooter() {
  return (
    <footer className="border-t border-border bg-surface">
      <div className="mx-auto grid w-full max-w-6xl grid-cols-2 gap-x-8 gap-y-10 px-6 py-16 sm:px-8 sm:grid-cols-3 lg:grid-cols-4 lg:px-10 lg:py-20">
        <div className="col-span-2 sm:col-span-3 lg:col-span-1">
          <Logo withSubtitle />
          <p className="mt-4 max-w-xs text-sm leading-[1.6] text-text-secondary">
            AI for patient access and the healthcare revenue cycle — with humans in control of
            what matters.
          </p>
        </div>
        {COLUMNS.map((col) => (
          <div key={col.title}>
            <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
              {col.title}
            </p>
            <ul className="mt-3 space-y-2">
              {col.links.map((l) => (
                <li key={l.label}>
                  <Link
                    to={l.to}
                    className="text-sm text-text-secondary hover:text-text-primary"
                  >
                    {l.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-border">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-1 px-6 py-6 text-xs text-text-muted sm:flex-row sm:items-center sm:justify-between sm:px-8 lg:px-10">
          <span>© {new Date().getFullYear()} Foresight. Anticipate. Automate. Advance Care.</span>
          <span>A more proactive healthcare system is possible.</span>
        </div>
      </div>
    </footer>
  );
}
