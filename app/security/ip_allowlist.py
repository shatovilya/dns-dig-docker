import ipaddress
from typing import Any

from fastapi import HTTPException, Request

from config import Settings
from security.client_ip import get_client_ip


def _parse_allowlist(cidrs: list[str]) -> list[Any]:
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


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def check_ip_allowed(
    request: Request,
    settings: Settings,
    allowlist: list[str],
    *,
    trust_internal: bool = False,
) -> None:
    """Raise 403 if client IP is not permitted by allowlist or internal-only policy."""
    client_ip = get_client_ip(request, settings)

    if allowlist:
        nets = _parse_allowlist(allowlist)
        if nets:
            try:
                addr = ipaddress.ip_address(client_ip)
            except ValueError:
                raise HTTPException(403, "forbidden")
            if any(addr in net for net in nets):
                return
            raise HTTPException(403, "forbidden")

    if trust_internal:
        if _is_private_ip(client_ip):
            return
        raise HTTPException(403, "forbidden")
