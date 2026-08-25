"""Airbnb URL normalization for worker pool (calendar vs pricing)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def normalize_airbnb_listing_url(url: str) -> str:
    """Clean .com room URL for US proxy (no tracking query)."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.replace("airbnb.ru", "airbnb.com")
    if not host.startswith("www.") and "airbnb.com" in host:
        host = "www." + host.lstrip(".")
    if "airbnb.com" not in host:
        host = host.replace("airbnb.ru", "www.airbnb.com")
    scheme = parsed.scheme or "https"
    path = parsed.path or "/"
    return urlunparse((scheme, host, path, "", "", ""))


def normalize_airbnb_pricing_url(url: str, *, currency: str = "THB") -> str:
    """Pricing via US proxy: .com + currency (not Agent1 .ru normalize)."""
    parsed = urlparse(normalize_airbnb_listing_url(url))
    query = parse_qs(parsed.query, keep_blank_values=True)
    if not (query.get("currency") or [""])[0].strip():
        query["currency"] = [currency.upper()]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
