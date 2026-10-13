"""
State management for application data.

Handles persistence and retrieval of application state including
screener runs, annotations, AI records, and simulation state.
"""

import json
import logging
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .models import (
    ScreenerRunDetail,
    StockAnnotation,
    AIAnalysisRecord,
)
from .sim_engine import SimAccountEngine

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages application state persistence.

    Provides thread-safe access to stored application data.
    """

    DEFAULT_STATE_DIR = Path.home() / ".tdx-trend"

    def __init__(self, state_dir: Path | None = None):
        """
        Initialize state manager.

        Args:
            state_dir: Directory for state files (defaults to ~/.tdx-trend)
        """
        self.state_dir = state_dir or self.DEFAULT_STATE_DIR
        self._lock = RLock()

        # State storage
        self._screener_runs: dict[str, ScreenerRunDetail] = {}
        self._annotations: dict[str, StockAnnotation] = {}
        self._ai_records: list[AIAnalysisRecord] = []
        self._sim_engine: SimAccountEngine | None = None

    # Screener run management
    def get_screener_run(self, run_id: str) -> ScreenerRunDetail | None:
        """Get screener run by ID."""
        with self._lock:
            return self._screener_runs.get(run_id)

    def list_screener_runs(self) -> list[ScreenerRunDetail]:
        """List all screener runs."""
        with self._lock:
            return list(self._screener_runs.values())

    def save_screener_run(self, run: ScreenerRunDetail) -> None:
        """Save screener run."""
        with self._lock:
            self._screener_runs[run.run_id] = run
            self._persist_screener_runs()

    def delete_screener_run(self, run_id: str) -> bool:
        """Delete screener run."""
        with self._lock:
            if run_id in self._screener_runs:
                del self._screener_runs[run_id]
                self._persist_screener_runs()
                return True
            return False

    def get_latest_screener_run(self) -> ScreenerRunDetail | None:
        """Get the most recent screener run."""
        with self._lock:
            if not self._screener_runs:
                return None
            runs = list(self._screener_runs.values())
            runs.sort(key=lambda r: r.created_at, reverse=True)
            return runs[0]

    # Annotation management
    def get_annotation(self, symbol: str) -> StockAnnotation | None:
        """Get annotation for symbol."""
        with self._lock:
            return self._annotations.get(symbol)

    def list_annotations(self) -> list[StockAnnotation]:
        """List all annotations."""
        with self._lock:
            return list(self._annotations.values())

    def save_annotation(self, annotation: StockAnnotation) -> StockAnnotation:
        """Save annotation for symbol."""
        with self._lock:
            self._annotations[annotation.symbol] = annotation
            self._persist_annotations()
            return annotation

    def delete_annotation(self, symbol: str) -> bool:
        """Delete annotation for symbol."""
        with self._lock:
            if symbol in self._annotations:
                del self._annotations[symbol]
                self._persist_annotations()
                return True
            return False

    # AI record management
    def list_ai_records(
        self,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[AIAnalysisRecord]:
        """
        List AI analysis records.

        Args:
            symbol: Optional symbol filter
            limit: Maximum number of records to return

        Returns:
            List of AI records, sorted by fetched_at descending
        """
        with self._lock:
            records = self._ai_records
            if symbol:
                records = [r for r in records if r.symbol == symbol]
            records = sorted(records, key=lambda r: r.fetched_at, reverse=True)
            return records[:limit]

    def add_ai_record(self, record: AIAnalysisRecord) -> None:
        """Add AI analysis record."""
        with self._lock:
            self._ai_records.append(record)
            # Keep only recent records (limit to 500 per symbol)
            self._prune_ai_records()
            self._persist_ai_records()

    def delete_ai_record(
        self,
        symbol: str,
        fetched_at: str,
        provider: str | None = None,
    ) -> bool:
        """Delete AI analysis record."""
        with self._lock:
            original_count = len(self._ai_records)
            self._ai_records = [
                r for r in self._ai_records
                if not (
                    r.symbol == symbol
                    and r.fetched_at == fetched_at
                    and (provider is None or r.provider == provider)
                )
            ]
            deleted = len(self._ai_records) < original_count
            if deleted:
                self._persist_ai_records()
            return deleted

    def _prune_ai_records(self, max_per_symbol: int = 500) -> None:
        """Remove old AI records to prevent unbounded growth."""
        # Group by symbol
        by_symbol: dict[str, list[AIAnalysisRecord]] = {}
        for record in self._ai_records:
            if record.symbol not in by_symbol:
                by_symbol[record.symbol] = []
            by_symbol[record.symbol].append(record)

        # Keep only recent records per symbol
        pruned = []
        for symbol, records in by_symbol.items():
            records.sort(key=lambda r: r.fetched_at, reverse=True)
            pruned.extend(records[:max_per_symbol])

        self._ai_records = pruned

    # Simulation engine management
    def get_sim_engine(self) -> SimAccountEngine:
        """Get or create simulation engine."""
        with self._lock:
            if self._sim_engine is None:
                self._sim_engine = self._load_sim_engine()
            return self._sim_engine

    def reset_sim_engine(self) -> SimAccountEngine:
        """Reset simulation engine to initial state."""
        with self._lock:
            self._sim_engine = SimAccountEngine()
            self._persist_sim_engine()
            return self._sim_engine

    def save_sim_engine(self, engine: SimAccountEngine) -> None:
        """Save simulation engine state."""
        with self._lock:
            self._sim_engine = engine
            self._persist_sim_engine()

    # Persistence methods
    def load_all_state(self) -> None:
        """Load all persisted state from disk."""
        self._load_screener_runs()
        self._load_annotations()
        self._load_ai_records()
        self._sim_engine = self._load_sim_engine()

    def persist_all_state(self) -> None:
        """Persist all current state to disk."""
        self._persist_screener_runs()
        self._persist_annotations()
        self._persist_ai_records()
        if self._sim_engine:
            self._persist_sim_engine()

    def _screener_runs_path(self) -> Path:
        return self.state_dir / "screener_runs.json"

    def _annotations_path(self) -> Path:
        return self.state_dir / "annotations.json"

    def _ai_records_path(self) -> Path:
        return self.state_dir / "ai_records.json"

    def _sim_engine_path(self) -> Path:
        return self.state_dir / "sim_state.json"

    def _load_screener_runs(self) -> None:
        """Load screener runs from disk."""
        path = self._screener_runs_path()
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._screener_runs = {
                run_id: ScreenerRunDetail(**run_data)
                for run_id, run_data in data.items()
            }
            logger.info(f"Loaded {len(self._screener_runs)} screener runs")
        except Exception as e:
            logger.error(f"Failed to load screener runs: {e}")

    def _persist_screener_runs(self) -> None:
        """Persist screener runs to disk."""
        path = self._screener_runs_path()
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                run_id: run.dict()
                for run_id, run in self._screener_runs.items()
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to persist screener runs: {e}")

    def _load_annotations(self) -> None:
        """Load annotations from disk."""
        path = self._annotations_path()
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._annotations = {
                symbol: StockAnnotation(**annot_data)
                for symbol, annot_data in data.items()
            }
            logger.info(f"Loaded {len(self._annotations)} annotations")
        except Exception as e:
            logger.error(f"Failed to load annotations: {e}")

    def _persist_annotations(self) -> None:
        """Persist annotations to disk."""
        path = self._annotations_path()
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                symbol: annot.dict()
                for symbol, annot in self._annotations.items()
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to persist annotations: {e}")

    def _load_ai_records(self) -> None:
        """Load AI records from disk."""
        path = self._ai_records_path()
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._ai_records = [AIAnalysisRecord(**item) for item in data]
            logger.info(f"Loaded {len(self._ai_records)} AI records")
        except Exception as e:
            logger.error(f"Failed to load AI records: {e}")

    def _persist_ai_records(self) -> None:
        """Persist AI records to disk."""
        path = self._ai_records_path()
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            data = [record.dict() for record in self._ai_records]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to persist AI records: {e}")

    def _load_sim_engine(self) -> SimAccountEngine:
        """Load simulation engine from disk."""
        path = self._sim_engine_path()
        if not path.exists():
            return SimAccountEngine()

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            engine = SimAccountEngine()
            # Restore state
            engine._cash = data.get("cash", engine._cash)
            engine._initial_capital = data.get("initial_capital", engine._initial_capital)
            engine._as_of_date = data.get("as_of_date", engine._as_of_date)
            engine._orders = [engine._order_from_dict(o) for o in data.get("orders", [])]
            engine._fills = [engine._fill_from_dict(f) for f in data.get("fills", [])]
            logger.info("Loaded simulation engine state")
            return engine
        except Exception as e:
            logger.error(f"Failed to load sim engine: {e}")
            return SimAccountEngine()

    def _persist_sim_engine(self) -> None:
        """Persist simulation engine to disk."""
        if not self._sim_engine:
            return

        path = self._sim_engine_path()
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "cash": self._sim_engine._cash,
                "initial_capital": self._sim_engine._initial_capital,
                "as_of_date": self._sim_engine._as_of_date,
                "orders": [o.dict() for o in self._sim_engine._orders],
                "fills": [f.dict() for f in self._sim_engine._fills],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to persist sim engine: {e}")


def create_state_manager(state_dir: str | None = None) -> StateManager:
    """
    Factory function to create StateManager.

    Args:
        state_dir: Optional path to state directory

    Returns:
        StateManager instance
    """
    path = Path(state_dir) if state_dir else None
    return StateManager(state_dir=path)
