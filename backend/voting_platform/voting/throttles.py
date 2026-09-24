from common.throttles import IPThrottle, PhoneThrottle, UserOrIPThrottle


class OTPRequestPhoneShortThrottle(PhoneThrottle):
    scope = "otp_request_phone_short"


class OTPRequestPhoneDayThrottle(PhoneThrottle):
    scope = "otp_request_phone_day"


class OTPRequestIPThrottle(IPThrottle):
    scope = "otp_request_ip"


class OTPVerifyPhoneThrottle(PhoneThrottle):
    scope = "otp_verify_phone"


class OTPVerifyIPThrottle(IPThrottle):
    scope = "otp_verify_ip"


class VoteVoterThrottle(UserOrIPThrottle):
    scope = "vote_voter"


class VoteIPThrottle(IPThrottle):
    scope = "vote_ip"
