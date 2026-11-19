"""Shared list pagination (D68): `?page=` and `?per=` (10/25/50, default 10)."""

from django.core.paginator import Paginator

PAGE_SIZES = (10, 25, 50)


def paginate(request, queryset, default=10):
    try:
        per = int(request.GET.get("per", default))
    except (TypeError, ValueError):
        per = default
    if per not in PAGE_SIZES:
        per = default
    paginator = Paginator(queryset, per)
    page = paginator.get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    page.querystring = query.urlencode()
    page.per = per
    page.page_sizes = PAGE_SIZES
    return page
