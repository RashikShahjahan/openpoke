from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import RowMapping

from .models import TriggerRecord
from .utils import to_storage_timestamp, utc_now


_metadata = MetaData()
_triggers = Table(
    "triggers",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_name", Text, nullable=False),
    Column("payload", Text, nullable=False),
    Column("start_time", Text),
    Column("next_trigger", Text),
    Column("recurrence_rule", Text),
    Column("timezone", Text),
    Column("status", Text, nullable=False, server_default="active"),
    Column("last_error", Text),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Index("idx_triggers_agent_next", "agent_name", "next_trigger"),
)


class TriggerStore:
    """Low-level persistence for triggers backed by SQLAlchemy."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._engine = create_engine(
            f"sqlite:///{db_path}", future=True, connect_args={"timeout": 30}
        )
        self._lock = threading.Lock()
        self._ensure_directory()
        self._ensure_schema()

    def _ensure_directory(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_schema(self) -> None:
        with self._lock:
            _metadata.create_all(self._engine, tables=[_triggers])

    def insert(self, payload: Dict[str, Any]) -> int:
        with self._lock, self._engine.begin() as conn:
            result = conn.execute(insert(_triggers).values(**payload))
            return int(result.inserted_primary_key[0])

    def fetch_one(self, trigger_id: int, agent_name: str) -> Optional[TriggerRecord]:
        with self._lock, self._engine.connect() as conn:
            row = conn.execute(
                select(_triggers).where(
                    _triggers.c.id == trigger_id,
                    _triggers.c.agent_name == agent_name,
                )
            ).mappings().first()
        return self._row_to_record(row) if row else None

    def update(self, trigger_id: int, agent_name: str, fields: Dict[str, Any]) -> bool:
        if not fields:
            return False
        payload = {
            **fields,
            "updated_at": to_storage_timestamp(utc_now()),
        }
        with self._lock, self._engine.begin() as conn:
            result = conn.execute(
                update(_triggers)
                .where(_triggers.c.id == trigger_id, _triggers.c.agent_name == agent_name)
                .values(**payload)
            )
            return bool(result.rowcount)

    def list_for_agent(self, agent_name: str) -> List[TriggerRecord]:
        with self._lock, self._engine.connect() as conn:
            rows = conn.execute(
                select(_triggers)
                .where(_triggers.c.agent_name == agent_name)
                .order_by(_triggers.c.next_trigger.is_(None), _triggers.c.next_trigger)
            ).mappings().all()
        return [self._row_to_record(row) for row in rows]

    def fetch_due(
        self, agent_name: Optional[str], before_iso: str
    ) -> List[TriggerRecord]:
        query = select(_triggers).where(
            _triggers.c.status == "active",
            _triggers.c.next_trigger.is_not(None),
            _triggers.c.next_trigger <= before_iso,
        )
        if agent_name:
            query = query.where(_triggers.c.agent_name == agent_name)
        query = query.order_by(_triggers.c.next_trigger, _triggers.c.id)
        with self._lock, self._engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [self._row_to_record(row) for row in rows]

    def clear_all(self) -> None:
        with self._lock, self._engine.begin() as conn:
            conn.execute(delete(_triggers))

    def _row_to_record(self, row: RowMapping) -> TriggerRecord:
        data = dict(row)
        return TriggerRecord.model_validate(data)


__all__ = ["TriggerStore"]
