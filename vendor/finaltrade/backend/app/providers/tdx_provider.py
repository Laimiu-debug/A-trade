"""
TDX (TongDaXing) data provider implementation.
"""

from typing import Optional

from .base import MarketDataProvider
from ..models import CandlePoint
from ..tdx_loader import load_candles_for_symbol, load_input_pool_from_tdx


class TDXProvider(MarketDataProvider):
    """
    Market data provider using TDX (TongDaXing) local files.
    """

    def __init__(self, tdx_root: str):
        """
        Initialize TDX provider.

        Args:
            tdx_root: Root directory of TDX data files
        """
        self.tdx_root = tdx_root

    def get_candles(self, symbol: str, start_date: str, end_date: str) -> list[CandlePoint]:
        """
        Get candlestick data from TDX files.

        Args:
            symbol: Stock symbol (e.g., "sh600519")
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of candlestick data points
        """
        candles = load_candles_for_symbol(self.tdx_root, symbol)
        # Filter by date range
        filtered = [
            c
            for c in candles
            if start_date <= c["time"] <= end_date
        ]
        return filtered

    def get_symbol_name(self, symbol: str) -> Optional[str]:
        """
        Get symbol name from TDX data.

        Note: TDX files don't contain names by default.
        This method returns None and names should be fetched
        from other sources like quote APIs.

        Args:
            symbol: Stock symbol

        Returns:
            None (names not available in TDX files)
        """
        # TDX files don't contain stock names
        # Names should be fetched from quote APIs
        return None

    def load_input_pool(self, markets: list[str], as_of_date: str) -> list[dict[str, str]]:
        """
        Load input pool from TDX files.

        Args:
            markets: List of market codes (e.g., ["sh", "sz"])
            as_of_date: As-of date in YYYY-MM-DD format

        Returns:
            List of stock dictionaries with symbol and metadata
        """
        return load_input_pool_from_tdx(
            tdx_root=self.tdx_root,
            markets=markets,
            as_of_date=as_of_date,
        )
