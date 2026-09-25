from django.apps import AppConfig
from django.core.checks import Error, register


@register()
def check_trusted_proxies(app_configs, **kwargs):
    """A malformed TRUSTED_PROXIES must fail at startup, never be silently ignored."""
    from django.conf import settings

    from common.ip import is_valid_proxy_entry

    errors = []
    for entry in getattr(settings, "TRUSTED_PROXIES", []) or []:
        if not is_valid_proxy_entry(entry):
            errors.append(
                Error(
                    f"TRUSTED_PROXIES entry {entry!r} is not an IP address or CIDR network.",
                    hint="Use e.g. 10.0.0.5 or 10.0.0.0/8, outermost proxy first.",
                    id="common.E001",
                )
            )
    return errors


class CommonConfig(AppConfig):
    name = "common"
