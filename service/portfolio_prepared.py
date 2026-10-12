"""One-cutoff immutable index cache. It never caches fills or account state."""
from types import SimpleNamespace

FIELDS = ("securities", "bars", "by_date", "calendar_supplied", "all_dates", "calendar_index",
          "benchmark_by_date", "data_digest", "fingerprint", "scorer", "action_schedule", "history_counts", "st_counts", "config")


class PreparedCache:
    def __init__(self):
        self.key, self.view = None, None

    def create(self, engine_class, dataset, config, state=None):
        from .portfolio_engine import _digest
        key = (dataset.get("fingerprint") or _digest(dataset), config.get("endDate"))
        if self.key == key and self.view is not None and hasattr(engine_class, "_validate_dataset"):
            engine = engine_class(dataset, config, state=state, _shared=self.view)
        else:
            engine = engine_class(dataset, config, state=state)
        if all(hasattr(engine, key) for key in FIELDS):
            self.key = key
            self.view = SimpleNamespace(**{key: getattr(engine, key) for key in FIELDS})
        return engine
