import { Navigate, Route, Routes } from "react-router-dom";

import { RequireReady, RequireSession } from "./components/ProtectedRoute";
import { MarketingLayout } from "./marketing/MarketingLayout";
import { HowItWorksPage } from "./marketing/pages/HowItWorksPage";
import { LandingPage } from "./marketing/pages/LandingPage";
import { PricingPage } from "./marketing/pages/PricingPage";
import { ProductPage } from "./marketing/pages/ProductPage";
import { ResourcesPage } from "./marketing/pages/ResourcesPage";
import { SolutionsPage } from "./marketing/pages/SolutionsPage";
import { AcceptInvitePage } from "./pages/AcceptInvitePage";
import { AppointmentDetailPage } from "./pages/AppointmentDetailPage";
import { AppointmentsPage } from "./pages/AppointmentsPage";
import { ClaimDetailPage } from "./pages/ClaimDetailPage";
import { ClaimsPage } from "./pages/ClaimsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { EligibilityCheckDetailPage } from "./pages/EligibilityCheckDetailPage";
import { LoginPage } from "./pages/LoginPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SignupPage } from "./pages/SignupPage";
import { StubPage } from "./pages/StubPage";
import { TeamPage } from "./pages/TeamPage";

export default function App() {
  return (
    <Routes>
      {/* ---------- PUBLIC marketing site — "/" is always this, auth or not ---------- */}
      <Route element={<MarketingLayout />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/product" element={<ProductPage />} />
        <Route path="/solutions" element={<SolutionsPage />} />
        <Route path="/how-it-works" element={<HowItWorksPage />} />
        <Route path="/resources" element={<ResourcesPage />} />
        <Route path="/pricing" element={<PricingPage />} />
      </Route>

      {/* ---------- Auth ---------- */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route element={<RequireSession />}>
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/accept-invite" element={<AcceptInvitePage />} />
      </Route>

      {/* ---------- The authenticated app, under /app/* ---------- */}
      <Route path="/app" element={<RequireReady />}>
        <Route index element={<DashboardPage />} />
        <Route path="claims" element={<ClaimsPage />} />
        <Route path="claims/:id" element={<ClaimDetailPage />} />
        <Route path="appointments" element={<AppointmentsPage />} />
        <Route path="appointments/:id" element={<AppointmentDetailPage />} />
        <Route path="eligibility/:id" element={<EligibilityCheckDetailPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="team" element={<TeamPage />} />
        <Route
          path="patients"
          element={
            <StubPage
              title="Patients"
              blurb="A unified patient record — demographics, coverage, visit history, and the AI's per-patient risk flags."
            />
          }
        />
        <Route
          path="insurance"
          element={
            <StubPage
              title="Insurance"
              blurb="Payer directory, plan rules, and real-time eligibility beyond the appointment view."
            />
          }
        />
        <Route
          path="front-desk"
          element={
            <StubPage
              title="Front Desk"
              blurb="Check-in queue, intake forms, and the day's schedule at a glance."
            />
          }
        />
        <Route
          path="tasks"
          element={
            <StubPage
              title="Tasks"
              blurb="Everything the AI has surfaced for a human — denials to prevent, follow-ups to make, refills to review."
            />
          }
        />
        <Route
          path="reports"
          element={
            <StubPage
              title="Reports"
              blurb="Revenue-cycle KPIs, denial trends, and automation impact over time."
            />
          }
        />
      </Route>

      {/* stale bookmarks from before the /app move */}
      <Route path="/dashboard" element={<Navigate to="/app" replace />} />
      <Route path="/claims" element={<Navigate to="/app/claims" replace />} />
      <Route path="/appointments" element={<Navigate to="/app/appointments" replace />} />
      <Route path="/settings" element={<Navigate to="/app/settings" replace />} />
      <Route path="/team" element={<Navigate to="/app/team" replace />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
