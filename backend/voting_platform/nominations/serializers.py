from django.core.validators import URLValidator
from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from audit import services as audit
from audit.models import AuditAction
from common.exceptions import DuplicateNomination, NominationsClosed
from common.phone import InvalidPhoneNumber, normalize_phone
from common.serializers import PlainTextMixin
from common.text import clean_text
from events.models import Award, EventStatus

from .images import InvalidImage, process_image
from .models import Nomination, NominationStatus, Nominee

LINK_FIELDS = ("website_url", "instagram_url", "facebook_url", "x_url", "youtube_url", "tiktok_url")
PUBLIC_NOMINEE_FIELDS = ["id", "name", "stage_name", "bio", "photo", *LINK_FIELDS]


class AwardRefSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    event = serializers.SlugRelatedField(source="category.event", slug_field="slug", read_only=True)

    class Meta:
        model = Award
        fields = ["id", "name", "slug", "category", "event"]
        read_only_fields = fields

    def get_category(self, award) -> dict:
        return {
            "id": str(award.category_id),
            "name": award.category.name,
            "slug": award.category.slug,
        }


class PublicNomineeSerializer(serializers.ModelSerializer):
    """Public nominee data only. Contact details and owner are never included."""

    class Meta:
        model = Nominee
        fields = PUBLIC_NOMINEE_FIELDS
        read_only_fields = fields


class PublicNominationSerializer(serializers.ModelSerializer):
    award = AwardRefSerializer(read_only=True)
    nominee = PublicNomineeSerializer(read_only=True)

    class Meta:
        model = Nomination
        fields = ["id", "award", "nominee"]
        read_only_fields = fields


class SubmissionResultSerializer(PublicNominationSerializer):
    """What a submitter gets back: the public shape plus the (pending) status."""

    class Meta(PublicNominationSerializer.Meta):
        fields = ["id", "status", "award", "nominee"]
        read_only_fields = fields


class NomineeNominationSerializer(serializers.ModelSerializer):
    award = AwardRefSerializer(read_only=True)

    class Meta:
        model = Nomination
        fields = ["id", "award"]
        read_only_fields = fields


class NomineeProfileSerializer(PublicNomineeSerializer):
    nominations = serializers.SerializerMethodField()

    class Meta(PublicNomineeSerializer.Meta):
        fields = [*PUBLIC_NOMINEE_FIELDS, "nominations"]
        read_only_fields = fields

    @extend_schema_field(NomineeNominationSerializer(many=True))
    def get_nominations(self, nominee):
        return NomineeNominationSerializer(
            getattr(nominee, "visible_nominations", []), many=True, context=self.context
        ).data


class StaffNomineeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Nominee
        fields = [*PUBLIC_NOMINEE_FIELDS, "contact_phone", "contact_email", "owner"]
        read_only_fields = fields


class StaffNominationSerializer(serializers.ModelSerializer):
    award = AwardRefSerializer(read_only=True)
    nominee = StaffNomineeSerializer(read_only=True)
    reviewed_by = serializers.SlugRelatedField(slug_field="email", read_only=True)

    class Meta:
        model = Nomination
        fields = [
            "id",
            "status",
            "award",
            "nominee",
            "submitted_by_ip",
            "reviewed_by",
            "reviewed_at",
            "rejection_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class RejectSerializer(PlainTextMixin, serializers.Serializer):
    plain_text_fields = ("reason",)
    reason = serializers.CharField(max_length=500)


_HTTP_ONLY = URLValidator(schemes=["http", "https"])


def _link_field():
    return serializers.URLField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=300,
        validators=[_HTTP_ONLY],
    )


class NominationSubmitSerializer(PlainTextMixin, serializers.Serializer):
    """Public nomination form (multipart). Creates a Nominee and a *pending* Nomination."""

    plain_text_fields = ("name", "stage_name")
    multiline_fields = ("bio",)

    award = serializers.UUIDField()
    name = serializers.CharField(max_length=120)
    stage_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    bio = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    photo = serializers.FileField()
    website_url = _link_field()
    instagram_url = _link_field()
    facebook_url = _link_field()
    x_url = _link_field()
    youtube_url = _link_field()
    tiktok_url = _link_field()
    contact_phone = serializers.CharField(max_length=32, required=False, allow_blank=True)
    contact_email = serializers.EmailField(max_length=254, required=False, allow_blank=True)
    captcha_token = serializers.CharField(
        max_length=2048, required=False, allow_blank=True, write_only=True
    )

    def validate_award(self, award_id):
        award = (
            Award.objects.select_related("category__event")
            .filter(pk=award_id, is_active=True)
            .exclude(category__event__status=EventStatus.DRAFT)
            .first()
        )
        if award is None:
            raise serializers.ValidationError("Unknown award.")
        return award

    def validate_contact_phone(self, value):
        if not value:
            return ""
        try:
            return normalize_phone(value)
        except InvalidPhoneNumber as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_photo(self, value):
        try:
            return process_image(value)
        except InvalidImage as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        attrs = super().validate(attrs)
        award = attrs["award"]
        if not award.category.event.nominations_are_open():
            raise NominationsClosed()
        if not (attrs.get("contact_email") or attrs.get("contact_phone")):
            raise serializers.ValidationError(
                {"contact_email": "Provide a contact email or phone number."}
            )
        for field in LINK_FIELDS:
            if not attrs.get(field):
                attrs[field] = None
        attrs["bio"] = clean_text(attrs.get("bio", ""), multiline=True)
        duplicate = Nomination.objects.filter(
            award=award,
            nominee__name__iexact=attrs["name"],
            status__in=[NominationStatus.PENDING, NominationStatus.APPROVED],
        ).exists()
        if duplicate:
            raise DuplicateNomination()
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        award = validated_data.pop("award")
        validated_data.pop("captcha_token", None)
        from common.ip import get_client_ip

        with transaction.atomic():
            nominee = Nominee.objects.create(**validated_data)
            nomination = Nomination.objects.create(
                nominee=nominee,
                award=award,
                status=NominationStatus.PENDING,
                submitted_by_ip=get_client_ip(request),
            )
            audit.log(
                AuditAction.NOMINATION_SUBMITTED,
                request=request,
                target=nomination,
                metadata={"award_id": str(award.pk), "nominee_id": str(nominee.pk)},
            )
        return nomination
