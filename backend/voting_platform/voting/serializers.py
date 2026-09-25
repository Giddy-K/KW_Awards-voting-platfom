from django.conf import settings
from rest_framework import serializers

from common.phone import InvalidPhoneNumber, normalize_phone
from common.serializers import PlainTextMixin

from .models import Vote


class _PhoneMixin(serializers.Serializer):
    phone = serializers.CharField(max_length=32)

    def validate_phone(self, value):
        try:
            return normalize_phone(value)
        except InvalidPhoneNumber as exc:
            raise serializers.ValidationError(str(exc)) from exc


class OTPRequestSerializer(serializers.Serializer):
    # Deliberately NOT _PhoneMixin: the OTP request endpoint applies its own, stricter
    # region/type check (Phase 2.2, common.phone.normalize_phone_strict via
    # voting.views.OTPRequestView) so every kind of rejection -- garbage input, a Kenyan
    # landline, a foreign mobile, a premium-rate number -- returns the one specific
    # `unsupported_phone_number` code, not a mix of generic field errors and that code.
    phone = serializers.CharField(max_length=32)
    captcha_token = serializers.CharField(max_length=2048, required=False, allow_blank=True)


class OTPVerifySerializer(_PhoneMixin):
    code = serializers.RegexField(regex=r"^\d+$", min_length=6, max_length=6)

    def validate_code(self, value):
        if len(value) != settings.OTP_LENGTH:
            raise serializers.ValidationError(f"Enter the {settings.OTP_LENGTH}-digit code.")
        return value


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


class VoterTokenSerializer(serializers.Serializer):
    token = serializers.CharField()
    token_type = serializers.CharField()
    expires_in = serializers.IntegerField(help_text="Lifetime in seconds.")


class VoteCastSerializer(serializers.Serializer):
    """Only the nomination is accepted. Award and event are derived server-side (AUDIT F-08)."""

    nomination_id = serializers.UUIDField()


class VoteReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vote
        fields = ["id", "award", "nomination", "created_at"]
        read_only_fields = fields


class MyVoteSerializer(serializers.ModelSerializer):
    """A voter's own vote: which award and nominee, never any tallies."""

    award = serializers.SerializerMethodField()
    nomination = serializers.SerializerMethodField()
    voided = serializers.BooleanField(source="is_voided", read_only=True)

    class Meta:
        model = Vote
        fields = ["id", "award", "nomination", "voided", "created_at"]
        read_only_fields = fields

    def get_award(self, vote) -> dict:
        return {
            "id": str(vote.award_id),
            "name": vote.award.name,
            "event": vote.event.slug,
        }

    def get_nomination(self, vote) -> dict:
        return {"id": str(vote.nomination_id), "nominee": vote.nomination.nominee.name}


class StaffVoteSerializer(serializers.ModelSerializer):
    event = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    award = serializers.SerializerMethodField()
    nomination = serializers.SerializerMethodField()
    voter_phone = serializers.CharField(source="voter.masked_phone", read_only=True)
    voided_by = serializers.SlugRelatedField(slug_field="email", read_only=True)

    class Meta:
        model = Vote
        fields = [
            "id",
            "event",
            "award",
            "nomination",
            "voter_phone",
            "source",
            "quantity",
            "ip_address",
            "user_agent",
            "created_at",
            "voided_at",
            "voided_by",
            "void_reason",
        ]
        read_only_fields = fields

    def get_award(self, vote) -> dict:
        return {"id": str(vote.award_id), "name": vote.award.name}

    def get_nomination(self, vote) -> dict:
        return {"id": str(vote.nomination_id), "nominee": vote.nomination.nominee.name}


class VoidSerializer(PlainTextMixin, serializers.Serializer):
    plain_text_fields = ("reason",)
    reason = serializers.CharField(max_length=500)


# --- results and stats (documented shapes) -------------------------------------


class ResultNomineeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    stage_name = serializers.CharField()
    photo = serializers.CharField(allow_null=True)


class ResultEntrySerializer(serializers.Serializer):
    nomination_id = serializers.UUIDField()
    nominee = ResultNomineeSerializer()
    votes = serializers.IntegerField()
    rank = serializers.IntegerField()


class ResultAwardSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    total_votes = serializers.IntegerField()
    nominations = ResultEntrySerializer(many=True)


class ResultCategorySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    slug = serializers.CharField()
    awards = ResultAwardSerializer(many=True)


class ResultEventSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()
    year = serializers.IntegerField()
    status = serializers.CharField()
    results_published_at = serializers.DateTimeField(allow_null=True)


class ResultsSerializer(serializers.Serializer):
    event = ResultEventSerializer()
    generated_at = serializers.DateTimeField()
    categories = ResultCategorySerializer(many=True)


class StatsTotalsSerializer(serializers.Serializer):
    votes = serializers.IntegerField()
    voters = serializers.IntegerField()
    voided_votes = serializers.IntegerField()
    pending_nominations = serializers.IntegerField()
    approved_nominations = serializers.IntegerField()


class StatsDaySerializer(serializers.Serializer):
    date = serializers.DateField()
    votes = serializers.IntegerField()


class StatsCategorySerializer(serializers.Serializer):
    category_id = serializers.UUIDField()
    category = serializers.CharField()
    votes = serializers.IntegerField()
    voters = serializers.IntegerField()


class StatsEventSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()


class StatsSerializer(serializers.Serializer):
    event = StatsEventSerializer()
    totals = StatsTotalsSerializer()
    votes_over_time = StatsDaySerializer(many=True)
    per_category = StatsCategorySerializer(many=True)
