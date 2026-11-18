
import json
from datetime import datetime, timezone
from pathlib import Path


class TraceLogger:
    """Write agent execution events to a JSONL trace file."""

    def __init__(self, output_dir="out"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.trace_path = self.output_dir / "trace.jsonl"

        # Start a fresh trace for each agent run.
        self.trace_path.write_text(
            "",
            encoding="utf-8",
        )

    def log(self, event, **details):
        """Append a timestamped event to the trace."""

        record = {
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "event": event,
            **details,
        }

        with self.trace_path.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
            )

        return record
