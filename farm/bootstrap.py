"""Reference data every environment needs: expense categories and role groups.

Used by the data migration (with historical models) and by `seed_demo`
(with live models). Both paths are idempotent.
"""

from django.utils.translation import gettext, gettext_noop as _

ROLE_OWNER = "OWNER"
ROLE_MANAGER = "MANAGER"
ROLE_WORKER = "WORKER"
ROLE_CASHIER = "CASHIER"
ROLES = [ROLE_OWNER, ROLE_MANAGER, ROLE_WORKER, ROLE_CASHIER]

# Built-in names are stored in Indonesian and marked for translation (gettext_noop): while an admin has not
# renamed one, `builtin_label` shows it in the user's language (D120). A renamed entry shows as typed.
EXPENSE_CATEGORIES = [
    ("BIBIT", _("Bibit")),
    ("PUPUK", _("Pupuk")),
    ("PESTISIDA", _("Pestisida")),
    ("TENAGA_KERJA", _("Tenaga Kerja")),
    ("UTILITAS", _("Utilitas")),
    ("PERALATAN", _("Peralatan")),
    ("LAINNYA", _("Lainnya")),
]


def user_has_role(user, *roles: str) -> bool:
    """True if `user` is a superuser or belongs to any of the given role groups."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=roles).exists()


def ensure_expense_categories(ExpenseCategory) -> int:
    created = 0
    for code, name in EXPENSE_CATEGORIES:
        _, was_created = ExpenseCategory.objects.get_or_create(code=code, defaults={"name": name})
        created += was_created
    return created


def ensure_role_groups(Group) -> int:
    created = 0
    for role in ROLES:
        _, was_created = Group.objects.get_or_create(name=role)
        created += was_created
    return created


PREHARVEST_CHECKLIST = [
    _("Masa henti pestisida (PHI) sudah terlewati"),
    _("Sampel buah memenuhi kematangan / brix target"),
    _("Alat panen, keranjang dan timbangan bersih"),
    _("Area sortir dan gudang siap"),
    _("Tenaga panen terjadwal"),
    # D130: basic food safety
    _("Air untuk mencuci dan menyemprot bersih"),
    _("Toilet dan tempat cuci tangan tersedia untuk pekerja"),
    _("Tidak ada pekerja sakit yang ikut memanen"),
    _("Wadah panen bersih dan tidak dipakai untuk bahan kimia"),
]
FOOD_SAFETY_LINES = PREHARVEST_CHECKLIST[5:]

BUILTIN_TEXTS = frozenset([name for _code, name in EXPENSE_CATEGORIES] + PREHARVEST_CHECKLIST)


def builtin_label(text: str) -> str:
    """A built-in name in the active language; anything an admin typed is shown unchanged (D120)."""
    return gettext(text) if text in BUILTIN_TEXTS else text


def ensure_preharvest_checklist(PreHarvestChecklistItem) -> int:
    """Seed the default checklist only when none exist (admin may edit freely afterwards)."""
    if PreHarvestChecklistItem.objects.exists():
        return 0
    for i, text in enumerate(PREHARVEST_CHECKLIST, start=1):
        PreHarvestChecklistItem.objects.create(text=text, order=i * 10)
    return len(PREHARVEST_CHECKLIST)
