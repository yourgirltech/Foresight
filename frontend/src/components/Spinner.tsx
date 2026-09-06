export function FullPageSpinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex h-full min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-3 text-text-secondary">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border-strong border-t-accent" />
        <p className="text-sm">{label}</p>
      </div>
    </div>
  );
}
