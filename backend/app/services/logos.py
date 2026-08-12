# Company-logo lookup for ticker folder cards, via Logo.dev's ticker-image
# CDN (https://www.logo.dev/docs/logo-images/ticker) — one image URL per
# ticker symbol, no separate JSON call needed.
#
# A ticker with this app's own exchange-suffix convention (.TO, .LN, .SA,
# .AU, .TSX, .CN, etc. — see ticker_registry.py) is only ever queried WITH
# that suffix attached, never as the bare symbol alone. Logo.dev's bare
# lookup silently defaults to US markets, so querying a foreign-suffixed
# ticker's bare symbol risks matching a completely different, US-listed
# company that happens to share the same letters (confirmed empirically:
# bare "ARE" resolves to an unrelated US company, not whatever "ZRE.TO"
# actually is) — worse than showing no logo at all. If our suffix isn't one
# Logo.dev itself recognizes as a real exchange code, the lookup just comes
# back empty, which is the correct, safe outcome, not a bug to work around.
# Anything without a match — a theme folder (parenthesized name, never
# alnum-only), an unlisted/private company, or an API failure — simply has
# no entry in the result; callers treat that the same as "no logo
# available" and fall back to the plain folder icon.

import concurrent.futures
import re

import requests

from app.config import settings

# Group 1: the bare ticker. Group 2 (optional): its exchange suffix, if
# any. Anything that doesn't fully match — spaces, parentheses (theme
# folders), multiple dots — is left alone rather than guessed at.
_TICKER_PATTERN = re.compile(r"^([A-Za-z0-9]+)(?:\.([A-Za-z0-9]+))?$")

# In-process cache, alive for as long as the server is — logos don't
# change often enough to justify a fresh Logo.dev call on every page load.
# Resets on redeploy/restart, which is fine; the next request just repopulates it.
_cache: dict[str, str | None] = {}


def _logo_url(symbol: str) -> str:
    # `token` is a Logo.dev *publishable* key — safe to embed in a URL the
    # browser itself loads (see their docs), unlike a real secret key.
    # `fallback=404` is what makes a miss a real 404 instead of a generic
    # monogram placeholder — without it, every ticker would technically
    # "have a logo," defeating the point of only showing real ones.
    return f"https://img.logo.dev/ticker/{symbol}?token={settings.logo_dev_api_key}&fallback=404&format=png&retina=true"


def _fetch_logo(ticker: str) -> str | None:
    if ticker in _cache:
        return _cache[ticker]
    logo = None
    match = _TICKER_PATTERN.match(ticker)
    if match:
        bare, suffix = match.group(1), match.group(2)
        symbol = f"{bare}.{suffix}" if suffix else bare
        url = _logo_url(symbol)
        try:
            response = requests.get(url, timeout=5)
            if response.ok:
                logo = url
        except requests.RequestException:
            logo = None
    _cache[ticker] = logo
    return logo


def get_logos_for(tickers: list[str]) -> dict[str, str]:
    """Best-effort {ticker: logo_url} for every ticker in `tickers` that
    actually has one — tickers with no logo aren't included at all, not
    given a null/None value. Looked up in parallel (a thread pool, not
    asyncio — this whole app is sync) so a cold cache right after a
    restart doesn't serialize ~100 individual Logo.dev round-trips into one
    slow request; a warm cache returns instantly regardless."""
    if not settings.logo_dev_api_key or not tickers:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = pool.map(_fetch_logo, tickers)
    return {ticker: logo for ticker, logo in zip(tickers, results) if logo}
