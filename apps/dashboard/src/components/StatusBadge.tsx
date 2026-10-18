import { humanize, toneFor } from "../services/format";
import type { StatusTone } from "../types/dashboard";

interface StatusBadgeProps {
  status: string | null;
  /** Shown when `status` is null (nothing has happened yet). */
  fallback?: string;
  tone?: StatusTone;
}

/** A small coloured pill for a plan, execution or verification status. */
export function StatusBadge({ status, fallback = "Not yet", tone }: StatusBadgeProps) {
  const resolvedTone = tone ?? toneFor(status);
  return (
    <span className={`badge badge-${resolvedTone}`}>{status === null ? fallback : humanize(status)}</span>
  );
}
