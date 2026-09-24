from rest_framework import serializers

from common.text import clean_text


class PlainTextMixin:
    """Store selected string fields as plain text (tags and control characters removed).

    List field names in ``plain_text_fields`` (single line) and ``multiline_fields``
    (newlines kept). A required field that becomes empty after cleaning is rejected.
    """

    plain_text_fields = ()
    multiline_fields = ()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        errors = {}
        for names, multiline in ((self.plain_text_fields, False), (self.multiline_fields, True)):
            for name in names:
                if name not in attrs or attrs[name] is None:
                    continue
                cleaned = clean_text(attrs[name], multiline=multiline)
                field = self.fields.get(name)
                if not cleaned and field is not None and not getattr(field, "allow_blank", False):
                    errors[name] = "This field may not be blank."
                attrs[name] = cleaned
        if errors:
            raise serializers.ValidationError(errors)
        return attrs
