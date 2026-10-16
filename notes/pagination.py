from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    """Page-number pagination with a caller-controlled, bounded page size.

    Offset pagination is wrong for a feed that changes while you're reading
    it, but notes are listed newest-first and edited rarely, so the page
    drift that keyset pagination solves is not a problem we have yet.
    """

    page_size_query_param = "page_size"
    max_page_size = 100
