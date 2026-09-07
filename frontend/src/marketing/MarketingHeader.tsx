import { useEffect, useRef, useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { ChevronDown } from "lucide-react";

import { Logo, btnOutline } from "./components";

interface DropItem {
  label: string;
  to: string;
  desc: string;
}

const SOLUTIONS: DropItem[] = [
  { label: "For Clinics", to: "/solutions#clinics", desc: "Independent and group practices" },
  { label: "For Hospitals", to: "/solutions#hospitals", desc: "Departments and health systems" },
];

const RESOURCES: DropItem[] = [
  { label: "Guides", to: "/resources#guides", desc: "Playbooks for revenue-cycle teams" },
  { label: "How It Works", to: "/how-it-works", desc: "The agent architecture, in plain terms" },
  { label: "Product updates", to: "/resources#updates", desc: "What's shipping and what's next" },
];

const NAV_PRIMARY = "text-[15px] font-medium";
const NAV_QUIET = "text-[15px] font-normal";

function NavDropdown({
  label,
  items,
  quiet = false,
}: {
  label: string;
  items: DropItem[];
  quiet?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1 whitespace-nowrap transition-colors hover:text-text-primary ${
          quiet ? `${NAV_QUIET} text-text-muted` : `${NAV_PRIMARY} text-text-secondary`
        }`}
      >
        {label}
        <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      <div
        className={`absolute left-1/2 z-30 mt-4 w-72 origin-top -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-surface p-2 shadow-xl transition-all duration-150 ${
          open
            ? "pointer-events-auto scale-100 opacity-100"
            : "pointer-events-none scale-95 opacity-0"
        }`}
      >
        {items.map((it) => (
          <Link
            key={it.label}
            to={it.to}
            onClick={() => setOpen(false)}
            className="block rounded-lg px-3 py-3 transition-colors hover:bg-surface-muted"
          >
            <span className="block text-sm font-medium text-text-primary">{it.label}</span>
            <span className="mt-0.5 block text-xs text-text-secondary">{it.desc}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

const primaryLink = ({ isActive }: { isActive: boolean }) =>
  `${NAV_PRIMARY} whitespace-nowrap transition-colors ${
    isActive ? "text-text-primary" : "text-text-secondary hover:text-text-primary"
  }`;

const quietLink = ({ isActive }: { isActive: boolean }) =>
  `${NAV_QUIET} whitespace-nowrap transition-colors ${
    isActive ? "text-text-primary" : "text-text-muted hover:text-text-primary"
  }`;

export function MarketingHeader() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`sticky top-0 z-40 border-b transition-[background-color,border-color,box-shadow] duration-300 ${
        scrolled
          ? "border-border bg-surface/85 shadow-[0_1px_0_rgb(var(--color-border))] backdrop-blur-md"
          : "border-transparent bg-surface/60 backdrop-blur-sm"
      }`}
    >
      <div className="mx-auto flex h-20 w-full max-w-6xl items-center gap-8 px-6 sm:px-8 lg:px-10">
        <Logo withSubtitle />

        {/* nav gets to breathe now that the demo CTA lives only in the hero */}
        <nav className="mx-auto hidden items-center gap-8 lg:flex xl:gap-10">
          <NavLink to="/product" className={primaryLink}>
            Product
          </NavLink>
          <NavDropdown label="Solutions" items={SOLUTIONS} />
          <NavLink to="/how-it-works" className={primaryLink}>
            How It Works
          </NavLink>
          <NavDropdown label="Resources" items={RESOURCES} quiet />
          <NavLink to="/pricing" className={quietLink}>
            Pricing
          </NavLink>
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-4 lg:ml-0">
          <Link
            to="/login"
            className="whitespace-nowrap text-[15px] font-medium text-text-secondary transition-colors hover:text-text-primary"
          >
            Log in
          </Link>
          <Link to="/signup" className={`${btnOutline} whitespace-nowrap`}>
            Sign up
          </Link>
        </div>
      </div>
    </header>
  );
}
