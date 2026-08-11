# Company-logo lookup for ticker folder cards, via Finnhub's free
# company-profile endpoint (https://finnhub.io/docs/api/company-profile2).
# Coverage is genuinely partial — free-tier Finnhub only resolves
# currently-listed US-exchange tickers. A ticker with this app's own
# exchange-suffix convention (.TO, .LN, .SA, .AU, .CN, .TSX, etc. — see
# ticker_registry.py) never gets queried at all: guessing at a Finnhub
# symbol for one of these risks matching a completely different company by
# coincidence (there's no reliable way to tell "a real class-share suffix
# Finnhub understands" apart from "this app's own routing suffix" from the
# ticker string alone), which would be worse than just showing no logo.
# Anything without a match — a theme folder, an unlisted/private company,
# a delisted ticker, or a Finnhub API failure — simply has no entry in the
# result; callers treat that the same as "no logo available" and fall back
# to the plain folder icon.

import concurrent.futures
import re

import requests

from app.config import settings

_PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"

# Only ever query a ticker with no dot in it at all — see the module
# docstring above for why a suffixed ticker is skipped rather than guessed
# at.
_QUERYABLE = re.compile(r"^[A-Za-z0-9]+$")

# In-process cache, alive for as long as the server is — logos don't
# change often enough to justify a fresh Finnhub call on every page load.
# Resets on redeploy/restart, which is fine; the next request just repopulates it.
_cache: dict[str, str | None] = {}


def _fetch_logo(ticker: str) -> str | None:
    if ticker in _cache:
        return _cache[ticker]
    logo = None
    if _QUERYABLE.match(ticker):
        try:
            response = requests.get(
                _PROFILE_URL, params={"symbol": ticker, "token": settings.finnhub_api_key}, timeout=5
            )
            if response.ok:
                logo = response.json().get("logo") or None
        except requests.RequestException:
            logo = None
    _cache[ticker] = logo
    return logo


def get_logos_for(tickers: list[str]) -> dict[str, str]:
    """Best-effort {ticker: logo_url} for every ticker in `tickers` that
    actually has one — tickers with no logo aren't included at all, not
    given a null/None value. Looked up in parallel (a thread pool, not
    asyncio — this whole app is sync) so a cold cache right after a
    restart doesn't serialize ~100 individual Finnhub round-trips into one
    slow request; a warm cache returns instantly regardless."""
    if not settings.finnhub_api_key or not tickers:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = pool.map(_fetch_logo, tickers)
    return {ticker: logo for ticker, logo in zip(tickers, results) if logo}
