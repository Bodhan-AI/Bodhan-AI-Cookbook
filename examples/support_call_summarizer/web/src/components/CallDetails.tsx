import type { CallRecord, Language } from "../api";
import { formatDateTime, formatDuration } from "../lib/format";
import { languageName } from "../lib/languages";
import { StatusBadge } from "./StatusBadge";

interface CallDetailsProps {
  record: CallRecord;
  languages: Language[];
}

export function CallDetails({ record, languages }: CallDetailsProps) {
  const rows: Array<[string, string]> = [
    ["File", record.source_file],
    ["Duration", formatDuration(record.duration_seconds)],
    ["Language", languageName(record.source_language, languages)],
    ["Processed", formatDateTime(record.processed_at)],
    ["Ticket ID", record.ticket_id || "—"],
    ["Agent", record.agent_name || "—"],
  ];
  if (record.speaker_count !== null) rows.push(["Speakers", String(record.speaker_count)]);

  return (
    <section className="card call-details" aria-labelledby="details-heading">
      <div className="call-details__header">
        <h3 id="details-heading" className="card__subtitle">
          Call details
        </h3>
        <StatusBadge status={record.status} />
      </div>
      <dl className="call-details__list">
        {rows.map(([term, value]) => (
          <div key={term} className="call-details__item">
            <dt>{term}</dt>
            <dd className={term === "File" ? "call-details__file" : undefined}>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
