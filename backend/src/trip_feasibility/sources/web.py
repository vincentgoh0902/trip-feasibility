"""Guarded fetch of public web pages (the closures check), and their text.

Only the URLs a web search returned are fetched, and only like this: https on port 443, a
host that resolves to public addresses only (no loopback, private, link-local or reserved
ranges, checked again on every redirect), our User-Agent, a time and size limit, and HTML
only. Anthropic's web fetch tool isn't used: fetched text must go only to the tool-less
extractor (decision 92), and these limits are ours to set.

Known gap: the address is checked before httpx connects, and httpx resolves the name again,
so a DNS answer that changes in between (rebinding) isn't caught.
"""

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from trip_feasibility.checks.deps import CheckDeps

MAX_REDIRECTS = 3
HTML_TYPES = ("text/html", "application/xhtml+xml")
_SKIPPED_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "table", "ul", "ol", "dd", "dt",
}  # fmt: skip


class FetchError(Exception):
    """A page we won't or couldn't read; the reason is safe to log."""


async def resolve(host: str) -> list[str]:
    """The host's addresses (tests replace this: fake hosts don't resolve)."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return [str(info[4][0]) for info in infos]


async def check_url(url: str) -> None:
    """FetchError unless the URL is https to a host with only public addresses."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError("not https")
    if parts.port not in (None, 443):
        raise FetchError("not port 443")
    if parts.username or parts.password:
        raise FetchError("credentials in the URL")
    host = parts.hostname
    if not host:
        raise FetchError("no host")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(a) for a in await resolve(host)]
        except (OSError, ValueError) as e:
            raise FetchError(f"couldn't resolve {host}") from e
    if not addresses or not all(a.is_global for a in addresses):
        raise FetchError(f"{host} is not a public address")


async def fetch_html(deps: CheckDeps, url: str) -> str:
    """The page's HTML, following at most MAX_REDIRECTS redirects, each one checked."""
    settings = deps.settings
    for _ in range(MAX_REDIRECTS + 1):
        await check_url(url)
        try:
            async with deps.http.stream(
                "GET",
                url,
                follow_redirects=False,
                timeout=settings.closures_fetch_timeout_s,
                headers={"Accept": "text/html,application/xhtml+xml"},
            ) as r:
                if r.is_redirect and "location" in r.headers:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise FetchError(f"HTTP {r.status_code}")
                media_type = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if media_type not in HTML_TYPES:
                    raise FetchError(f"not HTML ({media_type or 'no type'})")
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > settings.closures_fetch_max_bytes:
                        raise FetchError("page too large")
                return body.decode(r.encoding or "utf-8", errors="replace")
        except httpx.HTTPError as e:
            raise FetchError(f"request failed: {type(e).__name__}") from e
    raise FetchError("too many redirects")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_text(html: str, max_chars: int) -> str:
    """The visible text of a page (no scripts, styles or markup), whitespace squeezed, cut
    to max_chars. Hidden elements (display:none) still come through: the extractor's
    prompt treats everything as data."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    body = "\n".join(line for line in lines if line)
    title = re.sub(r"\s+", " ", parser.title).strip()
    return (f"{title}\n\n{body}" if title else body)[:max_chars]


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def same_site(url: str, website: str) -> bool:
    """Whether url is on the venue's own website (its host or a subdomain of it)."""
    if "://" not in website:
        website = "https://" + website
    a, b = host_of(url), host_of(website)
    return bool(a and b) and (a == b or a.endswith("." + b))
