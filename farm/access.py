"""Role-based access (D27): OWNER / MANAGER / WORKER / CASHIER via Django Groups.

| Role    | Access                                                        |
|---------|---------------------------------------------------------------|
| OWNER   | everything (+ user management in the admin)                   |
| MANAGER | everything incl. expense approval and voids                   |
| WORKER  | farm operations (activities, harvests, spoilage); no money    |
| CASHIER | POS register and receipts only                                |

Superusers pass every check (treated as OWNER).
"""

from django.utils.translation import gettext as _
from functools import cached_property

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

from .bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, ROLE_WORKER

FINANCE_ROLES = (ROLE_OWNER, ROLE_MANAGER)
FARM_ROLES = (ROLE_OWNER, ROLE_MANAGER, ROLE_WORKER)
POS_ROLES = (ROLE_OWNER, ROLE_MANAGER, ROLE_CASHIER)


class UserRoles:
    """Per-request role lookup (one groups query), exposed to templates as `roles`."""

    def __init__(self, user):
        self.user = user

    @cached_property
    def names(self) -> frozenset[str]:
        user = self.user
        if user is None or not user.is_authenticated:
            return frozenset()
        if user.is_superuser:
            return frozenset({ROLE_OWNER})
        return frozenset(user.groups.values_list("name", flat=True))

    def has(self, *roles: str) -> bool:
        if self.user is not None and self.user.is_authenticated and self.user.is_superuser:
            return True
        return bool(self.names & set(roles))

    # Template-friendly flags.
    @property
    def finance(self) -> bool:
        """Can see money and approve/void (OWNER, MANAGER)."""
        return self.has(*FINANCE_ROLES)

    @property
    def farm(self) -> bool:
        return self.has(*FARM_ROLES)

    @property
    def pos(self) -> bool:
        return self.has(*POS_ROLES)

    @property
    def label(self) -> str:
        for role in (ROLE_OWNER, ROLE_MANAGER, ROLE_WORKER, ROLE_CASHIER):
            if role in self.names:
                return role
        return "-"


def get_roles(request) -> UserRoles:
    if not hasattr(request, "_gotani_roles"):
        request._gotani_roles = UserRoles(request.user)
    return request._gotani_roles


def landing_url_name(roles: UserRoles) -> str | None:
    """Where a user starts after login."""
    if roles.farm:
        return "home"
    if roles.has(ROLE_CASHIER):
        return "pos:register"
    return None


class RoleRequiredMixin(AccessMixin):
    """Anonymous → login redirect; authenticated without a listed role → HTTP 403.

    Set `allowed_roles` on the view; `post_roles` (optional) narrows unsafe methods,
    e.g. a page WORKERs may read but only OWNER/MANAGER may act on.
    """

    allowed_roles: tuple[str, ...] = ()
    post_roles: tuple[str, ...] | None = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        roles = get_roles(request)
        required = self.allowed_roles
        if request.method not in ("GET", "HEAD", "OPTIONS") and self.post_roles is not None:
            required = self.post_roles
        if not roles.has(*required):
            raise PermissionDenied(_("Anda tidak memiliki akses ke halaman ini."))
        self.roles = roles
        return super().dispatch(request, *args, **kwargs)


def roles_context(request):
    """Context processor: `roles` (flags) for nav and money-hiding in templates."""
    return {"roles": get_roles(request)}
