export function FullPageSpinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex h-full min-h-screen items-center justify-center">
      <div className="flex flex-col items-center gap-3 text-slate-500">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500" />
        <p className="text-sm">{label}</p>
      </div>
    </div>
  );
}
