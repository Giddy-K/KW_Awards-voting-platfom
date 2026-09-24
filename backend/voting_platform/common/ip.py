"""Client IP resolution that never trusts forwarding headers blindly."""

import ipaddress

from django.conf import settings
from ipware import get_client_ip as _ipware_get_client_ip


def _in_trusted_proxies(address, trusted):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    for entry in trusted:
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def get_client_ip(request):
    """Return the client IP as a string, or ``None``.

    ``settings.TRUSTED_PROXIES`` (list of IPs/CIDRs) names the reverse proxies allowed to
    set ``X-Forwarded-For``. With none configured, or when the direct peer
    (``REMOTE_ADDR``) is not one of them, forwarding headers are ignored entirely and the
    peer address is used. Only when the peer is a trusted proxy is the header parsed
    (via django-ipware, requiring every proxy hop to be trusted).
    """
    remote = request.META.get("REMOTE_ADDR") or None
    trusted = list(getattr(settings, "TRUSTED_PROXIES", []) or [])
    if not trusted or not remote or not _in_trusted_proxies(remote, trusted):
        return remote
    ip, _routable = _ipware_get_client_ip(
        request,
        proxy_order="right-most",
        proxy_trusted_ips=trusted,
        request_header_order=["X_FORWARDED_FOR"],
        strict=True,
    )
    return ip or remote
