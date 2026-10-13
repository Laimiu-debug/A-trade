"""
Web evidence provider for collecting stock-related news and information.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from .base import WebEvidenceProvider
from ..utils.text_utils import TextProcessor, URLUtils

logger = logging.getLogger(__name__)


class RSSWebEvidenceProvider(WebEvidenceProvider):
    """
    Collects web evidence from RSS feeds.

    This provider fetches RSS feeds from configured news sources,
    filters them by domain and quality, and returns relevant articles.
    """

    def __init__(self, timeout: float = 10.0):
        """
        Initialize RSS web evidence provider.

        Args:
            timeout: HTTP request timeout in seconds
        """
        self.timeout = timeout

    def collect_evidence(
        self,
        symbol: str,
        queries: list[str],
        source_domains: set[str],
    ) -> list[dict[str, str]]:
        """
        Collect evidence from RSS feeds.

        Note: This is a simplified implementation. The full version would:
        1. Iterate through configured RSS feed URLs
        2. Fetch and parse each feed
        3. Filter articles by relevance to the symbol/queries
        4. Apply quality filters
        5. Return matching articles

        Args:
            symbol: Stock symbol to search for
            queries: Search queries (not used in RSS mode)
            source_domains: Domains to filter results

        Returns:
            List of evidence items with title, url, source, etc.
        """
        # This is a placeholder implementation
        # The full implementation would be similar to the _collect_web_evidence
        # method from InMemoryStore
        logger.info(f"Collecting RSS evidence for {symbol}")
        return []

    def fetch_rss_feed(
        self,
        feed_url: str,
        domain_filter: set[str],
    ) -> list[dict[str, str]]:
        """
        Fetch and parse a single RSS feed.

        Args:
            feed_url: URL of the RSS feed
            domain_filter: Allowed domains (empty = all allowed)

        Returns:
            List of articles with title, url, source, pub_date
        """
        try:
            timeout = httpx.Timeout(self.timeout, connect=5.0)
            with httpx.Client(timeout=timeout) as client:
                response = client.get(feed_url)
                response.raise_for_status()

                return self._parse_rss_xml(response.text, domain_filter)

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch RSS feed {feed_url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing RSS feed {feed_url}: {e}")
            return []

    def _parse_rss_xml(
        self,
        xml_text: str,
        domain_filter: set[str],
    ) -> list[dict[str, str]]:
        """
        Parse RSS XML and extract article items.

        Args:
            xml_text: RSS feed XML content
            domain_filter: Allowed domains

        Returns:
            List of parsed articles
        """
        try:
            root = ET.fromstring(xml_text)
            items = []

            # Handle both RSS and Atom formats
            # RSS: <channel><item>
            # Atom: <entry>
            channel = root.find(".//channel") or root

            for item in channel.findall(".//item") + channel.findall(".//entry") + channel.findall(".//{http://www.w3.org/2005/Atom}entry"):
                try:
                    article = self._parse_rss_item(item, domain_filter)
                    if article:
                        items.append(article)
                except Exception as e:
                    logger.debug(f"Failed to parse RSS item: {e}")
                    continue

            return items

        except ET.ParseError as e:
            logger.error(f"Failed to parse RSS XML: {e}")
            return []

    def _parse_rss_item(
        self,
        item: ET.Element,
        domain_filter: set[str],
    ) -> dict[str, str] | None:
        """
        Parse a single RSS item into an article dictionary.

        Args:
            item: XML element for the item
            domain_filter: Allowed domains

        Returns:
            Article dict or None if filtered out
        """
        # Extract fields (handle both RSS and Atom formats)
        title = self._get_text(item, "title") or self._get_text(item, "{http://www.w3.org/2005/Atom}title")
        link = self._get_text(item, "link") or self._get_text(item, "{http://www.w3.org/2005/Atom}link")
        desc = self._get_text(item, "description") or self._get_text(item, "summary") or self._get_text(item, "{http://www.w3.org/2005/Atom}summary")
        pub_date = self._get_text(item, "pubDate") or self._get_text(item, "published") or self._get_text(item, "{http://www.w3.org/2005/Atom}published")
        source = self._get_text(item, "source") or self._get_text(item, "author") or ""

        if not title or not link:
            return None

        # Clean title and description
        title = TextProcessor.clean_event_text(title)
        desc = TextProcessor.clean_event_text(desc)

        # Filter by domain if specified
        if domain_filter and not URLUtils.url_in_domains(link, domain_filter):
            return None

        # Filter low quality sources
        if TextProcessor.is_low_quality_source(source, link):
            return None

        # Filter low signal titles
        if TextProcessor.is_low_signal_title(title):
            return None

        # Parse publication date
        parsed_date = self._parse_rss_date(pub_date)

        return {
            "title": title,
            "url": link,
            "source_name": source,
            "snippet": desc[:200],
            "pub_date": parsed_date,
        }

    def _get_text(self, element: ET.Element, tag: str) -> str:
        """Safely get text from an XML element."""
        child = element.find(tag)
        if child is not None:
            return child.text or ""
        return ""

    def _parse_rss_date(self, date_text: str) -> str:
        """
        Parse RSS date string to ISO format.

        Args:
            date_text: Date string from RSS feed

        Returns:
            ISO formatted date string or empty string
        """
        if not date_text:
            return ""

        try:
            parsed = parsedate_to_datetime(date_text)
            if parsed:
                return parsed.strftime("%Y-%m-%d")
        except Exception:
            pass

        # Try common formats
        formats = [
            "%Y-%m-%d",
            "%Y%m%d",
            "%Y/%m/%d",
        ]
        for fmt in formats:
            try:
                parsed = datetime.strptime(date_text.split()[0], fmt)
                return parsed.strftime("%Y-%m-%d")
            except ValueError:
                continue

        return ""


class SearchWebEvidenceProvider(WebEvidenceProvider):
    """
    Collects web evidence using web search APIs.

    This provider uses web search to find relevant articles
    about a stock symbol.
    """

    def __init__(self, api_key: str = "", timeout: float = 10.0):
        """
        Initialize search web evidence provider.

        Args:
            api_key: API key for search service
            timeout: HTTP request timeout
        """
        self.api_key = api_key
        self.timeout = timeout

    def collect_evidence(
        self,
        symbol: str,
        queries: list[str],
        source_domains: set[str],
    ) -> list[dict[str, str]]:
        """
        Collect evidence using web search.

        Args:
            symbol: Stock symbol
            queries: Search queries
            source_domains: Domains to filter results

        Returns:
            List of evidence items
        """
        if not self.api_key:
            logger.warning("No API key configured for search provider")
            return []

        all_results = []

        for query in queries[:3]:  # Limit to 3 queries
            try:
                results = self._search(query, source_domains)
                all_results.extend(results)
            except Exception as e:
                logger.error(f"Search failed for query '{query}': {e}")
                continue

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for result in all_results:
            url = result.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_results.append(result)

        return unique_results[:10]  # Limit to 10 results

    def _search(
        self,
        query: str,
        source_domains: set[str],
    ) -> list[dict[str, str]]:
        """
        Perform a web search query.

        This is a placeholder - implement based on your search API.
        """
        # Placeholder implementation
        # In production, this would call a search API like:
        # - Google Custom Search API
        # - Bing Search API
        # - SerpAPI
        # etc.

        logger.info(f"Searching for: {query}")
        return []

    def _filter_by_domains(
        self,
        results: list[dict[str, str]],
        domain_filter: set[str],
    ) -> list[dict[str, str]]:
        """Filter search results by domain."""
        if not domain_filter:
            return results

        return [
            r for r in results
            if URLUtils.url_in_domains(r.get("url", ""), domain_filter)
        ]


def create_rss_provider(timeout: float = 10.0) -> RSSWebEvidenceProvider:
    """Factory function to create RSS provider."""
    return RSSWebEvidenceProvider(timeout=timeout)


def create_search_provider(api_key: str = "", timeout: float = 10.0) -> SearchWebEvidenceProvider:
    """Factory function to create search provider."""
    return SearchWebEvidenceProvider(api_key=api_key, timeout=timeout)
