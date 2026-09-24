"""Client IP resolution that never trusts forwarding headers blindly."""

import copy
import ipaddress

from django.conf import settings
from ipware import get_client_ip as _ipware_get_client_ip

_FORWARDED_META_KEY = "HTTP_X_FORWARDED_FOR"


def is_valid_proxy_entry(entry):
    """``TRUSTED_PROXIES`` entries must be a plain IP address or a CIDR network."""
    try:
        ipaddress.ip_network(entry, strict=False)
    except (ValueError, TypeError):
        return False
    return True


def _matches(address, entry):
    try:
        return ipaddress.ip_address(address) in ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return False


def get_client_ip(request):
    """Return the client IP as a string, or ``None``.

    ``settings.TRUSTED_PROXIES`` describes the reverse-proxy chain in front of the app as an
    ordered list of IPs/CIDRs, one entry per hop, **outermost first and ending with the proxy
    that connects to this server** (a single nginx: one entry).

    * No proxies configured, or the direct peer (``REMOTE_ADDR``) is not the last entry: the
      forwarding header is ignored and the peer address is returned. A client can therefore never
      choose its own IP by sending a header.
    * Otherwise the header chain, with the peer appended as the final hop, is handed to
      django-ipware in strict mode: the client is the address just before the trusted hops, and
      the chain must contain exactly those hops. A spoofed prefix (``6.6.6.6, <real client>``)
      breaks that, and we fall back to the proxy's address rather than believe it. Configure the
      proxy to overwrite (not append to) ``X-Forwarded-For``.
    """
    remote = request.META.get("REMOTE_ADDR") or None
    trusted = list(getattr(settings, "TRUSTED_PROXIES", []) or [])
    if not trusted or not remote or not _matches(remote, trusted[-1]):
        return remote
    forwarded = request.META.get(_FORWARDED_META_KEY, "").strip()
    if not forwarded:
        return remote
    probe = copy.copy(getattr(request, "_request", request))
    probe.META = {**probe.META, _FORWARDED_META_KEY: f"{forwarded}, {remote}"}
    ip, _routable = _ipware_get_client_ip(
        probe,
        request_header_order=[_FORWARDED_META_KEY],
        proxy_trusted_ips=trusted,
        strict=True,
    )
    return ip or remote
