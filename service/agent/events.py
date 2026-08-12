from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "needs_review"}


class AgentRunStore:
    """Persist run state and an append-only, user-visible event stream."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.state_path = directory / "agent_run.json"
        self.events_path = directory / "agent_events.jsonl"
        self.model_io_path = directory / "model_io.json"
        self._lock = threading.Lock()

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def write_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = self.now()
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.state_path)

    def read_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def update_state(self, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = self.read_state()
            state.update(changes)
            self.write_state(state)
            return state

    def emit(
        self,
        event_type: str,
        summary: str,
        *,
        data: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            sequence = 1
            if self.events_path.exists():
                with self.events_path.open("r", encoding="utf-8") as stream:
                    sequence += sum(1 for _ in stream)
            event = {
                "sequence": sequence,
                "type": event_type,
                "timestamp": self.now(),
                "summary": summary,
                "data": data or {},
            }
            if model:
                event["model"] = model
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            return event

    def read_events(self, after: int = 0) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.events_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = json.loads(line)
                if int(event.get("sequence", 0)) > after:
                    events.append(event)
        return events

    def write_model_io(self, interactions: list[dict[str, Any]]) -> None:
        self.model_io_path.write_text(
            json.dumps(interactions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
