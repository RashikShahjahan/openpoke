from __future__ import annotations

import hashlib
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .models import WebPageSnapshot
from .utils import to_storage_timestamp, utc_now


DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_SNAPSHOT_CHARS = 60000
USER_AGENT = "OpenPokeWatcher/0.1 (+https://github.com/shlokkhemani/OpenPoke)"


class WebPageSnapshotError(RuntimeError):
    """Raised when a page cannot be fetched or converted into a snapshot."""


async def fetch_web_page_snapshot(url: str) -> WebPageSnapshot:
    """Fetch a URL and return a cleaned, hashable text snapshot."""

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html, text/plain;q=0.9, */*;q=0.8"},
    ) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebPageSnapshotError(f"Failed to fetch {url}: {exc}") from exc

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type or _looks_like_html(response.text):
        title, content = _extract_html_text(response.text)
    elif "text/" in content_type or not content_type:
        title = None
        content = _normalize_text(response.text)
    else:
        raise WebPageSnapshotError(f"Unsupported content type for {url}: {content_type}")

    if not content:
        raise WebPageSnapshotError(f"No readable text found at {url}")

    content = content[:MAX_SNAPSHOT_CHARS]
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return WebPageSnapshot(
        url=str(response.url),
        title=title,
        content=content,
        content_hash=content_hash,
        fetched_at=to_storage_timestamp(utc_now()),
    )


def summarize_initial_snapshot(snapshot: WebPageSnapshot) -> str:
    """Create a compact deterministic baseline summary for storage."""

    heading = snapshot.title.strip() if snapshot.title else snapshot.url
    excerpt = snapshot.content[:1000].strip()
    return f"{heading}\n\n{excerpt}"


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*(html|body|main|article|div|p|title)\b", text, re.IGNORECASE))


def _extract_html_text(html: str) -> tuple[Optional[str], str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()

    title = _normalize_text(soup.title.get_text(" ")) if soup.title else None
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n")
    return title or None, _normalize_text(text)


def _normalize_text(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


__all__ = [
    "WebPageSnapshotError",
    "fetch_web_page_snapshot",
    "summarize_initial_snapshot",
]
