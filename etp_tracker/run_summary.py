"""
Pipeline run summary and metrics tracking.

Records what happened during each pipeline run for
operational visibility and debugging.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

SUMMARY_FILENAME = "_run_summary.json"


@dataclass
class RunMetrics:
    started_at: str = ""
    finished_at: str = ""
    trusts_processed: int = 0
    new_filings: int = 0
    skipped_filings: int = 0
    errors: int = 0
    retried: int = 0
    strategies: dict = field(default_factory=dict)
    duration_seconds: float = 0.0

    def start(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        if self.started_at:
            try:
                start = datetime.fromisoformat(self.started_at)
                end = datetime.fromisoformat(self.finished_at)
                self.duration_seconds = round((end - start).total_seconds(), 1)
            except (ValueError, TypeError):
                pass

    def add_strategy(self, name: str, count: int = 1) -> None:
        self.strategies[name] = self.strategies.get(name, 0) + count

    def summary_line(self) -> str:
        parts = [
            f"Processed {self.new_filings} new filings",
            f"(skipped {self.skipped_filings})",
        ]
        if self.strategies:
            strat_parts = [f"{v} {k}" for k, v in sorted(self.strategies.items())]
            parts.append(f"Strategies: {', '.join(strat_parts)}.")
        if self.errors:
            parts.append(f"{self.errors} errors.")
        parts.append(f"{self.duration_seconds}s")
        return " ".join(parts)


def save_run_summary(output_root: Path, metrics: RunMetrics) -> None:
    """Write run summary JSON to outputs/_run_summary.json."""
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / SUMMARY_FILENAME
    path.write_text(
        json.dumps(asdict(metrics), indent=2),
        encoding="utf-8",
    )


def load_last_run(output_root: Path) -> RunMetrics | None:
    """Load the most recent run summary, or None."""
    path = output_root / SUMMARY_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunMetrics(**{k: v for k, v in data.items() if k in RunMetrics.__dataclass_fields__})
    except (json.JSONDecodeError, OSError, TypeError):
        return None
