import django_filters as filters

from .models import Note


class NoteFilter(filters.FilterSet):
    """Query-string filters for the note list.

    `q` is the only one that is not a straight column match; it is defined
    on the queryset so the same search behaviour is available to anything
    else that needs it.
    """

    q = filters.CharFilter(method="filter_search", label="Search title and body")
    team = filters.CharFilter(field_name="team__slug", lookup_expr="exact")
    author = filters.CharFilter(field_name="author__username", lookup_expr="exact")
    updated_after = filters.IsoDateTimeFilter(
        field_name="updated_at", lookup_expr="gte"
    )

    class Meta:
        model = Note
        fields = ["visibility", "team", "author", "q", "updated_after"]

    def filter_search(self, queryset, name, value):
        return queryset.search(value)
