"""DNS readiness helpers for api.open-home.online (read-only)."""

from __future__ import annotations

import socket
from dataclasses import dataclass


PRODUCTION_DOMAIN = "api.open-home.online"
EXPECTED_IP = "72.60.108.152"


@dataclass(frozen=True)
class DnsReadiness:
    domain: str
    expected_ip: str
    resolved_ips: tuple[str, ...]
    status: str  # READY | WAITING | MISMATCH
    detail: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "READY"


def parse_dns_answers(ips: list[str] | tuple[str, ...], *, expected_ip: str = EXPECTED_IP) -> DnsReadiness:
    cleaned = tuple(sorted({(ip or "").strip() for ip in ips if (ip or "").strip()}))
    if not cleaned:
        return DnsReadiness(
            domain=PRODUCTION_DOMAIN,
            expected_ip=expected_ip,
            resolved_ips=(),
            status="WAITING",
            detail="NXDOMAIN_OR_NO_A",
        )
    if expected_ip in cleaned and len(cleaned) == 1:
        return DnsReadiness(
            domain=PRODUCTION_DOMAIN,
            expected_ip=expected_ip,
            resolved_ips=cleaned,
            status="READY",
            detail="",
        )
    if expected_ip in cleaned:
        return DnsReadiness(
            domain=PRODUCTION_DOMAIN,
            expected_ip=expected_ip,
            resolved_ips=cleaned,
            status="READY",
            detail="extra_records_present",
        )
    return DnsReadiness(
        domain=PRODUCTION_DOMAIN,
        expected_ip=expected_ip,
        resolved_ips=cleaned,
        status="MISMATCH",
        detail="unexpected_ip",
    )


def resolve_a_records(domain: str = PRODUCTION_DOMAIN) -> list[str]:
    try:
        infos = socket.getaddrinfo(domain, None, family=socket.AF_INET)
    except socket.gaierror:
        return []
    return sorted({item[4][0] for item in infos})


def check_dns(domain: str = PRODUCTION_DOMAIN, expected_ip: str = EXPECTED_IP) -> DnsReadiness:
    return parse_dns_answers(resolve_a_records(domain), expected_ip=expected_ip)
