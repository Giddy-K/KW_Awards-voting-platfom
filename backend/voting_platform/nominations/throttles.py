from common.throttles import IPThrottle


class NominationIPThrottle(IPThrottle):
    scope = "nomination_ip"
