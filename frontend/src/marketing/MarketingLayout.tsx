import { useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { DemoModalProvider } from "./DemoModal";
import { MarketingFooter } from "./MarketingFooter";
import { MarketingHeader } from "./MarketingHeader";

/**
 * The public marketing shell — a top nav, no sidebar. Completely separate from
 * the authenticated app's AppShell. Present on every marketing route.
 */
export function MarketingLayout() {
  const { pathname, hash } = useLocation();

  // scroll to top on route change (or to the #anchor if present)
  useEffect(() => {
    if (hash) {
      document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth" });
    } else {
      window.scrollTo(0, 0);
    }
  }, [pathname, hash]);

  return (
    <DemoModalProvider>
      <div className="flex min-h-screen flex-col bg-background">
        <MarketingHeader />
        <main className="flex-1">
          <Outlet />
        </main>
        <MarketingFooter />
      </div>
    </DemoModalProvider>
  );
}
