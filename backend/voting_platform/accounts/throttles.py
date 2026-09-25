from common.throttles import IPThrottle


class StaffLoginIPThrottle(IPThrottle):
    scope = "staff_login_ip"
