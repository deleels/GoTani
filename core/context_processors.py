from django.utils.functional import SimpleLazyObject

from .services import farm_is_pkp


def tax(request):
    """`farm_is_pkp` for templates (D80); lazy so pages that never ask cost no query."""
    return {"farm_is_pkp": SimpleLazyObject(farm_is_pkp)}
