"""The three preregistered filters; no parameter search or forward outcomes."""
from __future__ import annotations

from service.portfolio_signals import DailyScorer
from service.strategies import evaluate


CANDIDATES = (
    ("pullbackVolumeConfirm", "trendPullback", {"trendWindow": 80, "pullbackWindow": 10, "minDrawdown": .03, "maxDrawdown": .12}),
    ("firstBreakoutTenSessions", "trendBreakout", {"lookback": 40, "trendWindow": 80, "volumeRatio": 1.2}),
    ("momentumMarketUp", "momentum", {"lookback": 60, "trendWindow": 120, "minReturn": .05}),
)


class ExtensionScorer(DailyScorer):
    """Filter the complete base trigger set before the runner takes its Top5.

    Scores retain their original base-strategy meaning and tie ordering. The
    private baseline runner supplies control selection and all event execution.
    """
    def __init__(self, dataset, config):
        super().__init__(dataset, config)
        self.exchange_calendar = list(dataset["calendar"])
        self.exchange_index = {day: i for i, day in enumerate(self.exchange_calendar)}

    def _complete(self, series, day, preceding, *, include_today=True, positive_volume=True):
        index = self.exchange_index.get(day)
        if series is None or index is None or index < preceding:
            return None
        days = self.exchange_calendar[index - preceding:index + int(include_today)]
        positions = [series.by_date.get(value) for value in days]
        if any(value is None for value in positions):
            return None
        if positive_volume and any(series.bars[value]["volume"] <= 0 for value in positions):
            return None
        return positions

    def _accept(self, candidate, symbol, day, scored):
        series = self.series[symbol]
        if candidate == "pullbackVolumeConfirm":
            positions = self._complete(series, day, 20, include_today=False)
            if positions is None:
                return False
            average = sum(series.bars[i]["volume"] for i in positions) / 20
            return series.bars[series.by_date[day]]["volume"] / average >= 1.2
        if candidate == "firstBreakoutTenSessions":
            # Ten earlier evaluations each need their full 80 preceding
            # sessions plus its own close. A missing day is unknown, not false.
            positions = self._complete(series, day, 90)
            if positions is None:
                return False
            config = {"strategyIds": ["trendBreakout"], "strategyParams": {"trendBreakout": CANDIDATES[1][2]}}
            return all(evaluate(series.signal_bars, i, config) is None for i in positions[-11:-1])
        if candidate == "momentumMarketUp":
            regime = scored.get("regime", {})
            return (self.benchmark is not None and self.benchmark.security.get("symbol") == "sh000300"
                    and self._complete(self.benchmark, day, 60, positive_volume=False) is not None
                    and regime.get("status") == "observed" and regime.get("symbol") == "sh000300"
                    and regime.get("date") == day and regime.get("trend") == "up")
        raise ValueError("Unknown registered candidate")

    def daily(self, day):
        scored = super().daily(day)
        source = scored["strategies"]
        scored["strategies"] = {
            candidate: [row for row in source[base] if self._accept(candidate, row["symbol"], day, scored)]
            for candidate, base, _ in CANDIDATES
        }
        return scored
