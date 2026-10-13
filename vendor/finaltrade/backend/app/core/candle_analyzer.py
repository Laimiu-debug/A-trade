"""
Candlestick data analysis utilities.

Provides functions for analyzing candlestick patterns,
detecting breakouts, and calculating technical metrics.
"""

import logging
from typing import Any

from ..models import CandlePoint

logger = logging.getLogger(__name__)


class CandleAnalyzer:
    """
    Utility class for candlestick data analysis.

    Provides static methods for common candlestick analysis operations.
    """

    @staticmethod
    def safe_mean(values: list[float] | list[int]) -> float:
        """
        Calculate mean of values, handling empty lists.

        Args:
            values: List of numeric values

        Returns:
            Mean value or 0.0 if list is empty
        """
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    @staticmethod
    def align_date_to_candles(
        candles: list[CandlePoint],
        date_text: str,
    ) -> str:
        """
        Align a date to the closest candle date.

        Finds the most recent candle that is on or before the target date.

        Args:
            candles: List of candlestick data points
            date_text: Target date in YYYY-MM-DD format

        Returns:
            Aligned date from candles
        """
        if not candles:
            return date_text

        parsed_target = CandleAnalyzer._parse_date(date_text)
        if parsed_target is None:
            return candles[-1].time

        for candle in reversed(candles):
            parsed_candle = CandleAnalyzer._parse_date(candle.time)
            if parsed_candle and parsed_candle <= parsed_target:
                return candle.time

        return candles[0].time

    @staticmethod
    def slice_candles_as_of(
        candles: list[CandlePoint],
        as_of_date: str | None,
    ) -> tuple[list[CandlePoint], str | None]:
        """
        Slice candles to include only data up to as_of_date.

        Args:
            candles: List of candlestick data points
            as_of_date: Optional cutoff date

        Returns:
            Tuple of (sliced candles, resolved as_of_date)
        """
        if not candles:
            return [], None

        if not as_of_date:
            return candles, candles[-1].time

        aligned = CandleAnalyzer.align_date_to_candles(candles, as_of_date)

        for idx, point in enumerate(candles):
            if point.time == aligned:
                return candles[: idx + 1], aligned

        return candles, candles[-1].time

    @staticmethod
    def collect_volume_price_breakout_candidates(
        candles: list[CandlePoint],
        lookback: int = 55,
        max_items: int = 4,
    ) -> list[tuple[int, float, float, bool, bool]]:
        """
        Collect volume-price breakout candidates.

        Identifies days with significant price and volume movements
        that could be breakout points.

        Args:
            candles: List of candlestick data points
            lookback: Number of recent days to analyze
            max_items: Maximum number of candidates to return

        Returns:
            List of tuples: (index, day_return, volume_ratio10, is_breakout, is_washout_reversal)
        """
        if len(candles) < 20:
            return []

        start_idx = max(0, len(candles) - lookback)
        segment = candles[start_idx:]

        candidates = []

        for i, candle in enumerate(segment):
            idx = start_idx + i

            # Skip if not enough history
            if idx < 10:
                continue

            # Calculate day return
            prev = candles[idx - 1]
            day_return = (candle.close - prev.close) / prev.close if prev.close > 0 else 0

            # Calculate volume ratio vs 10-day average
            recent_volumes = [max(0, int(candles[j].volume)) for j in range(max(0, idx - 10), idx)]
            avg_volume = CandleAnalyzer.safe_mean(recent_volumes)
            volume_ratio10 = max(0, int(candle.volume)) / avg_volume if avg_volume > 0 else 0

            # Check for breakout (high volume + significant gain)
            is_breakout = (
                day_return >= 0.04
                and volume_ratio10 >= 1.5
            )

            # Check for washout reversal (down then up)
            is_washout_reversal = False
            if idx >= 3:
                # Check if this follows a decline
                prev_3_close = candles[idx - 3].close
                if prev.close < prev_3_close * 0.95:  # 5% decline over 3 days
                    # And now recovering
                    if candle.close > prev.close * 1.02:  # 2% recovery
                        is_washout_reversal = True

            candidates.append((idx, day_return, volume_ratio10, is_breakout, is_washout_reversal))

        # Sort by combined score (volume * return)
        candidates.sort(
            key=lambda x: (x[2] * x[1]) if x[1] > 0 else 0,
            reverse=True,
        )

        return candidates[:max_items]

    @staticmethod
    def build_recent_price_volume_snapshot(
        candles: list[CandlePoint],
        lookback: int = 16,
    ) -> str:
        """
        Build a snapshot of recent price and volume data.

        Useful for AI context and debugging.

        Args:
            candles: List of candlestick data points
            lookback: Number of recent days to include

        Returns:
            Formatted string with price and volume data
        """
        if not candles:
            return "no_data"

        segment = candles[-lookback:] if len(candles) > lookback else candles

        lines = []
        for candle in segment:
            lines.append(
                f"{candle.time} "
                f"O={candle.open:.2f} H={candle.high:.2f} "
                f"L={candle.low:.2f} C={candle.close:.2f} "
                f"V={int(candle.volume)}"
            )

        return "\n".join(lines)

    @staticmethod
    def infer_recent_rebreakout_index(candles: list[CandlePoint]) -> int | None:
        """
        Infer the most recent re-breakout index.

        A re-breakout is when price breaks above a previous high
        after a consolidation period.

        Args:
            candles: List of candlestick data points

        Returns:
            Index of recent re-breakout or None
        """
        if len(candles) < 70:
            return None

        # Focus on recent 45 bars
        start = max(0, len(candles) - 45)
        end = len(candles) - 2

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [max(0, int(c.volume)) for c in candles]

        # Find recent highs
        recent_high = max(highs[start:])
        recent_high_idx = highs.index(recent_high)

        # Check if this high broke previous resistance
        if recent_high_idx > start + 10:
            prior_high = max(highs[start:recent_high_idx - 5])
            if recent_high > prior_high * 1.01:  # 1% breakout
                # Check volume confirmation
                vol_at_break = volumes[recent_high_idx]
                avg_vol_before = CandleAnalyzer.safe_mean(volumes[recent_high_idx - 10:recent_high_idx])

                if vol_at_break > avg_vol_before * 1.2:
                    return recent_high_idx

        return None

    @staticmethod
    def adjust_to_cluster_lead_index(
        candles: list[CandlePoint],
        index: int,
    ) -> int:
        """
        Adjust an index to point to the cluster leader.

        If multiple breakouts occur close together, find the
        one with the strongest volume signal.

        Args:
            candles: List of candlestick data points
            index: Initial breakout index

        Returns:
            Adjusted index pointing to cluster leader
        """
        if not candles or index < 0 or index >= len(candles):
            return index

        # Look for other breakouts within 5 days
        window_start = max(0, index - 5)
        window_end = min(len(candles), index + 5)

        volumes = [max(0, int(c.volume)) for c in candles]

        # Find highest volume day in window
        max_vol_idx = window_start
        for i in range(window_start, window_end):
            if volumes[i] > volumes[max_vol_idx]:
                max_vol_idx = i

        return max_vol_idx

    @staticmethod
    def _parse_date(date_text: str) -> Any | None:
        """Parse date string to datetime object."""
        try:
            from datetime import datetime
            return datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            return None


def create_candle_analyzer() -> CandleAnalyzer:
    """Factory function to create CandleAnalyzer."""
    return CandleAnalyzer()
