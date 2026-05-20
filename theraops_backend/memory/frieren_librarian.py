from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass
class IncidentRecord:
    service: str
    root_cause: str
    fix: str
    created_at: str


class FrierenLibrarian:
    """
    JSON-backed incident memory stub.
    The public interface stays small so we can swap this for ChromaDB later.
    """

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self._lock = asyncio.Lock()
        self._incidents = self._load_from_disk()

    async def store_incident(
        self,
        service: str,
        root_cause: str,
        fix: str,
    ) -> IncidentRecord:
        incident = IncidentRecord(
            service=service.strip(),
            root_cause=root_cause.strip(),
            fix=fix.strip(),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        async with self._lock:
            self._incidents.append(incident)
            await asyncio.to_thread(self._save_to_disk)

        return incident

    async def query_similar(
        self,
        service: str,
        sample_messages: Sequence[str],
        limit: int = 3,
    ) -> list[IncidentRecord]:
        async with self._lock:
            candidates = [i for i in self._incidents if i.service == service]

            # Filter out incidents with no real resolution
            candidates = [i for i in candidates if i.fix and len(i.fix) > 20]

            # Score by word overlap with current error (sample_messages proxy)
            error_words = set(" ".join(sample_messages).lower().split())

            def score(incident: IncidentRecord) -> int:
                inc_words = set((incident.root_cause + " " + incident.fix).lower().split())
                return len(error_words & inc_words)

            ranked = sorted(candidates, key=score, reverse=True)
            return ranked[:limit]

    def _load_from_disk(self) -> list[IncidentRecord]:
        if not self.storage_path.exists():
            return []

        try:
            raw_items = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except OSError:
            logger.exception("Failed to read incident memory from %s", self.storage_path)
            return []
        except json.JSONDecodeError:
            self._quarantine_corrupt_file()
            return []

        if not isinstance(raw_items, list):
            logger.error("Incident memory file %s does not contain a JSON list.", self.storage_path)
            return []

        incidents: list[IncidentRecord] = []
        for item in raw_items:
            incidents.append(
                IncidentRecord(
                    service=item.get("service", ""),
                    root_cause=item.get("root_cause", ""),
                    fix=item.get("fix", ""),
                    created_at=item.get("created_at", ""),
                )
            )
        return incidents

    def _save_to_disk(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(incident) for incident in self._incidents]
        temp_path = self.storage_path.with_suffix(f"{self.storage_path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.storage_path)

    def _extract_keywords(self, text: str) -> set[str]:
        keywords = set()
        for word in text.lower().split():
            cleaned = "".join(char for char in word if char.isalnum())
            if len(cleaned) >= 4:
                keywords.add(cleaned)
        return keywords

    def _quarantine_corrupt_file(self) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        corrupt_path = self.storage_path.with_suffix(f"{self.storage_path.suffix}.corrupt-{timestamp}")

        try:
            self.storage_path.replace(corrupt_path)
            logger.error(
                "Incident memory file was corrupt and has been moved to %s",
                corrupt_path,
            )
        except OSError:
            logger.exception("Incident memory file is corrupt and could not be moved: %s", self.storage_path)
