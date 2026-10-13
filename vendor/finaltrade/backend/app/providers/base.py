"""
Base interfaces for data providers.
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import CandlePoint


class MarketDataProvider(ABC):
    """
    Abstract base class for market data providers.

    Implementations can fetch data from various sources like TDX files,
    APIs, or databases.
    """

    @abstractmethod
    def get_candles(self, symbol: str, start_date: str, end_date: str) -> list[CandlePoint]:
        """
        Get candlestick data for a symbol within a date range.

        Args:
            symbol: Stock symbol (e.g., "sh600519")
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            List of candlestick data points
        """
        pass

    @abstractmethod
    def get_symbol_name(self, symbol: str) -> Optional[str]:
        """
        Get the name of a stock symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Stock name or None if not found
        """
        pass

    @abstractmethod
    def load_input_pool(self, markets: list[str], as_of_date: str) -> list[dict[str, str]]:
        """
        Load the input pool of stocks for screening.

        Args:
            markets: List of market codes (e.g., ["sh", "sz"])
            as_of_date: As-of date in YYYY-MM-DD format

        Returns:
            List of stock dictionaries with symbol and metadata
        """
        pass


class AIProvider(ABC):
    """
    Abstract base class for AI analysis providers.

    Implementations can integrate with various AI services for
    stock analysis and insights.
    """

    @abstractmethod
    def analyze_stock(
        self,
        symbol: str,
        context: str,
        provider_config: dict,
    ) -> dict:
        """
        Analyze a stock using AI.

        Args:
            symbol: Stock symbol
            context: Context information for analysis
            provider_config: Provider configuration (API keys, etc.)

        Returns:
            Analysis result dictionary
        """
        pass

    @abstractmethod
    def test_provider(self, provider_config: dict) -> dict:
        """
        Test if the provider is accessible and properly configured.

        Args:
            provider_config: Provider configuration

        Returns:
            Test result dictionary
        """
        pass


class WebEvidenceProvider(ABC):
    """
    Abstract base class for web evidence providers.

    Implementations can fetch news, articles, and other web content
    for stock analysis.
    """

    @abstractmethod
    def collect_evidence(
        self,
        symbol: str,
        queries: list[str],
        source_domains: set[str],
    ) -> list[dict[str, str]]:
        """
        Collect web evidence for a stock.

        Args:
            symbol: Stock symbol
            queries: Search queries
            source_domains: Domains to filter results

        Returns:
            List of evidence items with title, url, source, etc.
        """
        pass
