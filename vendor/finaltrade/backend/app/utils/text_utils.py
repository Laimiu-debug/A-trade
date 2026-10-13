"""
Text processing utilities for cleaning, normalizing, and analyzing text content.
"""

import re
import html
from urllib.parse import urlparse
from typing import Callable


class TextProcessor:
    """Utility class for text processing operations."""

    @staticmethod
    def clean_whitespace(text: str) -> str:
        """Replace multiple whitespace characters with a single space and strip."""
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def strip_html_tags(text: str) -> str:
        """Remove HTML tags from text."""
        return re.sub(r"<[^>]+>", "", text)

    @staticmethod
    def extract_domain(raw_url: str) -> str:
        """Extract the domain from a URL."""
        parsed = urlparse(raw_url.strip())
        host = parsed.netloc.lower().strip()
        if host.startswith("www."):
            host = host[4:]
        return host

    @staticmethod
    def registrable_domain(host: str) -> str:
        """
        Extract the registrable domain (second-level domain + TLD).
        Handles special cases like .com.cn and .net.cn
        """
        parts = [part for part in host.lower().split(".") if part]
        if len(parts) <= 2:
            return ".".join(parts)
        if len(parts) >= 3 and parts[-2:] == ["com", "cn"]:
            return ".".join(parts[-3:])
        if len(parts) >= 3 and parts[-2:] == ["net", "cn"]:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])

    @staticmethod
    def is_low_quality_source(source_name: str, source_url: str) -> bool:
        """
        Check if a source is considered low quality based on keywords.
        Filters out user-generated content sites and forums.
        """
        haystack = f"{source_name} {source_url}".lower()
        blocked_keywords = (
            "guba",
            "mguba",
            "股吧",
            "财富号",
            "caifuhao",
            "论坛",
            "社区",
            "xueqiu",
            "雪球",
            "博客",
            "blog",
            "tieba",
        )
        return any(keyword in haystack for keyword in blocked_keywords)

    @staticmethod
    def is_low_signal_title(title: str) -> bool:
        """
        Check if a title has low signal value based on common noisy patterns.
        Filters out routine market commentary and generic analysis.
        """
        value = title.lower()
        noisy_keywords = (
            "早盘",
            "盘前",
            "盘后",
            "复盘",
            "午评",
            "收评",
            "走势",
            "观察分享",
            "股吧",
            "技术分析",
            "看盘",
            "点评",
            "开盘",
            "龙虎榜复盘",
            "强势涨停",
            "果断上车",
            "牛股",
            "主升浪",
            "标杆",
        )
        return any(keyword in value for keyword in noisy_keywords)

    @staticmethod
    def clean_event_text(text: str) -> str:
        """
        Clean and normalize event text by removing HTML, extra whitespace, and special formatting.
        """
        # Remove HTML entities and tags
        text = html.unescape(text)
        text = TextProcessor.strip_html_tags(text)
        # Clean whitespace
        text = TextProcessor.clean_whitespace(text)
        # Remove special formatting
        text = text.replace("　", " ")  # Full-width space
        text = text.strip()
        return text

    @staticmethod
    def normalize_rise_reasons(reasons: list[str]) -> list[str]:
        """
        Normalize and deduplicate rise reasons.

        Args:
            reasons: List of reason strings

        Returns:
            Cleaned and deduplicated list of reasons, limited to top 3 most relevant
        """
        if not reasons:
            return []

        # Clean each reason
        cleaned = []
        seen = set()
        for reason in reasons:
            # Clean whitespace and HTML
            clean_reason = TextProcessor.clean_event_text(reason)
            # Remove common prefixes
            clean_reason = re.sub(r"^(据悉|据悉，|据媒体报道|据公开信息等)+[：:]\s*", "", clean_reason)
            # Deduplicate
            if clean_reason and clean_reason not in seen:
                seen.add(clean_reason)
                cleaned.append(clean_reason)

        # Limit to top 3
        return cleaned[:3]

    @staticmethod
    def truncate_reason(text: str, max_len: int = 26) -> str:
        """
        Truncate text to maximum length while preserving word boundaries.
        Adds ellipsis if truncated.
        """
        if len(text) <= max_len:
            return text
        return text[: max_len - 1].rstrip() + "…"

    @staticmethod
    def extract_code_tokens(text: str) -> set[str]:
        """Extract stock code tokens (6-digit numbers) from text."""
        return set(re.findall(r"\b\d{6}\b", text))

    @staticmethod
    def compact_reason_by_keywords(
        text: str,
        extract_fn: Callable[[str], list[str]],
        transform_fn: Callable[[str], str] | None = None,
    ) -> str:
        """
        Compact a long text by extracting key information and applying transformations.

        Args:
            text: Input text to compact
            extract_fn: Function to extract key information
            transform_fn: Optional function to transform extracted items

        Returns:
            Compacted string with key information
        """
        # Extract key phrases using the provided function
        candidates = extract_fn(text)
        if not candidates:
            # Fallback to simple truncation if no extraction
            return TextProcessor.truncate_reason(text, max_len=26)

        # Transform items if transform function provided
        if transform_fn:
            transformed = [transform_fn(item) for item in candidates]
        else:
            transformed = candidates

        # Join with separators
        compacted = "；".join(transformed[:2])  # Limit to 2 items
        return TextProcessor.truncate_reason(compacted, max_len=26)

    @staticmethod
    def sanitize_ai_rise_reasons(
        reasons: list[str],
        extract_fn: Callable[[str], list[str]] | None = None,
        transform_fn: Callable[[str], str] | None = None,
        max_items: int = 3,
    ) -> list[str]:
        """
        Sanitize and normalize AI-generated rise reasons.

        Args:
            reasons: List of AI-generated reasons
            extract_fn: Optional function to extract key phrases
            transform_fn: Optional function to transform extracted phrases
            max_items: Maximum number of reasons to return

        Returns:
            Sanitized list of reasons
        """
        if not reasons:
            return []

        sanitized = []
        for reason in reasons:
            # Clean the reason
            clean_reason = TextProcessor.clean_event_text(reason)

            # Extract key phrases if extract_fn provided
            if extract_fn:
                extracted = extract_fn(clean_reason)
                if extracted:
                    # Transform and join
                    if transform_fn:
                        transformed = [transform_fn(item) for item in extracted]
                        clean_reason = "；".join(transformed[:2])
                    else:
                        clean_reason = "；".join(extracted[:2])

            # Truncate if too long
            clean_reason = TextProcessor.truncate_reason(clean_reason, max_len=26)

            if clean_reason and clean_reason not in sanitized:
                sanitized.append(clean_reason)

            if len(sanitized) >= max_items:
                break

        return sanitized

    @staticmethod
    def sanitize_theme_name(text: str) -> str:
        """
        Sanitize theme/topic name by removing common prefixes and cleaning formatting.
        """
        # Remove common prefixes
        text = re.sub(r"^(主题|概念|板块)?[：:]\s*", "", text)
        # Clean HTML and whitespace
        text = TextProcessor.clean_event_text(text)
        # Truncate if too long
        return TextProcessor.truncate_reason(text, max_len=20)


class URLUtils:
    """Utility class for URL-related operations."""

    @staticmethod
    def url_in_domains(url: str, domains: set[str]) -> bool:
        """
        Check if a URL belongs to any of the given domains.

        Args:
            url: URL to check
            domains: Set of domains to match against

        Returns:
            True if URL matches any domain, False otherwise
        """
        if not domains:
            return True

        host = urlparse(url).netloc.lower().strip()
        if host.startswith("www."):
            host = host[4:]

        if not host:
            return False

        host_root = TextProcessor.registrable_domain(host)
        return any(
            host == domain
            or host.endswith(f".{domain}")
            or (host_root and host_root == domain)
            for domain in domains
        )

    @staticmethod
    def source_domains(source_urls: list[str]) -> set[str]:
        """
        Extract all unique domains from a list of source URLs.

        Args:
            source_urls: List of source URLs

        Returns:
            Set of unique domains (including registrable domains)
        """
        domains: set[str] = set()
        for item in source_urls:
            domain = TextProcessor.extract_domain(item)
            if domain:
                domains.add(domain)
                root = TextProcessor.registrable_domain(domain)
                if root:
                    domains.add(root)
        return domains
