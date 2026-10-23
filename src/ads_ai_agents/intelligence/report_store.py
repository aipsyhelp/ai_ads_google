from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


class ReportStore:
    def __init__(self, root: Path | None = None) -> None:
        base = Path(__file__).resolve().parents[3]
        self.root = root or (base / "reports")

    def day_dir(self, day: date | None = None, *, kind: str = "audit") -> Path:
        day = day or date.today()
        if kind == "daily":
            path = self.root / "daily" / day.isoformat()
        elif kind == "changes":
            path = self.root / "changes" / day.isoformat()
        else:
            path = self.root / day.isoformat()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, directory: Path, name: str, payload: Any) -> Path:
        path = directory / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return path

    def write_csv(self, directory: Path, name: str, rows: list[dict[str, Any]]) -> Path:
        path = directory / name
        if not rows:
            path.write_text("", encoding="utf-8")
            return path
        # union of keys for stable header
        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: _csv_cell(row.get(k)) for k in fieldnames})
        return path

    def write_markdown(self, directory: Path, name: str, content: str) -> Path:
        path = directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def stamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
