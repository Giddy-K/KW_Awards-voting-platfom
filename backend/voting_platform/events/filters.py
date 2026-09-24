from django_filters import rest_framework as filters

from .models import Award, Category, Event


class EventFilter(filters.FilterSet):
    class Meta:
        model = Event
        fields = ["year", "status"]


class CategoryFilter(filters.FilterSet):
    event = filters.CharFilter(field_name="event__slug")

    class Meta:
        model = Category
        fields = ["event"]


class AwardFilter(filters.FilterSet):
    event = filters.CharFilter(field_name="category__event__slug")
    category = filters.UUIDFilter(field_name="category_id")

    class Meta:
        model = Award
        fields = ["event", "category", "is_active"]
