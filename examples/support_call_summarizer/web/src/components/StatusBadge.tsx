import type { CallStatus } from "../api";

const LABELS: Record<CallStatus, string> = {
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
  partial: "Partially completed",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: CallStatus }) {
  return <span className={`badge badge--${status}`}>{LABELS[status]}</span>;
}
