import ipaddress
from typing import Any

from fastapi import Request

from config import Settings


def _parse_networks(cidrs: list[str]) -> list[Any]:
    nets: list[Any] = []
    for cidr in cidrs:
        cidr = cidr.strip()
        if not cidr:
            continue
        try:
            nets.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return nets


def _ip_in_networks(ip: str, networks: list[Any]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in networks)


def get_trusted_proxy_networks(settings: Settings) -> list[Any]:
    return _parse_networks(settings.trust_proxy_ips)


def get_client_ip(request: Request, settings: Settings) -> str:
    """Resolve client IP; honor X-Forwarded-For only from trusted proxies."""
    direct = request.client.host if request.client else "unknown"
    if not settings.trust_proxy_enabled:
        return direct

    trusted = get_trusted_proxy_networks(settings)
    if not _ip_in_networks(direct, trusted):
        return direct

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # left-most is original client per common convention
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return direct


def is_https(request: Request, settings: Settings) -> bool:
    if settings.trust_proxy_enabled:
        proto = request.headers.get("x-forwarded-proto", "").lower()
        if proto == "https":
            return True
    return request.url.scheme == "https"
