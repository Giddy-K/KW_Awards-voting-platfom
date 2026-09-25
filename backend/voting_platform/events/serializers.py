from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.serializers import PlainTextMixin

from .models import Award, Category, Event, EventStatus


class NestedAwardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Award
        fields = ["id", "name", "slug", "description", "display_order", "is_active"]
        read_only_fields = fields


class _AwardsMixin(serializers.Serializer):
    """Adds ``awards`` to a category: active ones only unless the viewer is an event admin."""

    @extend_schema_field(NestedAwardSerializer(many=True))
    def get_awards(self, category):
        include_inactive = self.context.get("include_inactive", False)
        awards = [a for a in category.awards.all() if include_inactive or a.is_active]
        return NestedAwardSerializer(awards, many=True).data


class NestedCategorySerializer(_AwardsMixin, serializers.ModelSerializer):
    awards = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "description", "display_order", "awards"]
        read_only_fields = fields


class EventSerializer(PlainTextMixin, serializers.ModelSerializer):
    """Events. ``status`` is read-only here; change it with ``POST .../set-status/``."""

    plain_text_fields = ("name",)
    categories = serializers.SerializerMethodField()
    voting_is_open = serializers.SerializerMethodField()
    nominations_are_open = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id",
            "name",
            "slug",
            "year",
            "status",
            "nominations_open_at",
            "nominations_close_at",
            "voting_opens_at",
            "voting_closes_at",
            "results_published_at",
            "voting_is_open",
            "nominations_are_open",
            "categories",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "results_published_at",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(NestedCategorySerializer(many=True))
    def get_categories(self, event):
        return NestedCategorySerializer(
            event.categories.all(), many=True, context=self.context
        ).data

    def get_voting_is_open(self, event) -> bool:
        return event.voting_is_open()

    def get_nominations_are_open(self, event) -> bool:
        return event.nominations_are_open()

    def validate(self, attrs):
        attrs = super().validate(attrs)

        def value(name):
            return attrs[name] if name in attrs else getattr(self.instance, name, None)

        nom_open, nom_close = value("nominations_open_at"), value("nominations_close_at")
        vote_open, vote_close = value("voting_opens_at"), value("voting_closes_at")
        errors = {}
        if nom_open and nom_close and not nom_open < nom_close:
            errors["nominations_close_at"] = "Must be after nominations_open_at."
        if vote_open and vote_close and not vote_open < vote_close:
            errors["voting_closes_at"] = "Must be after voting_opens_at."
        if nom_close and vote_open and not nom_close <= vote_open:
            errors["voting_opens_at"] = "Voting cannot open before nominations close."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class SetStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=EventStatus.choices)


class CategorySerializer(_AwardsMixin, PlainTextMixin, serializers.ModelSerializer):
    plain_text_fields = ("name",)
    multiline_fields = ("description",)
    awards = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "event",
            "name",
            "slug",
            "description",
            "display_order",
            "awards",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "awards", "created_at", "updated_at"]


class AwardSerializer(PlainTextMixin, serializers.ModelSerializer):
    plain_text_fields = ("name",)
    multiline_fields = ("description",)
    event = serializers.SlugRelatedField(source="category.event", slug_field="slug", read_only=True)

    class Meta:
        model = Award
        fields = [
            "id",
            "category",
            "event",
            "name",
            "slug",
            "description",
            "display_order",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "event", "created_at", "updated_at"]
