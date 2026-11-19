"""Reference data every environment needs: expense categories and role groups.

Used by the data migration (with historical models) and by `seed_demo`
(with live models). Both paths are idempotent.
"""

ROLE_OWNER = "OWNER"
ROLE_MANAGER = "MANAGER"
ROLE_WORKER = "WORKER"
ROLE_CASHIER = "CASHIER"
ROLES = [ROLE_OWNER, ROLE_MANAGER, ROLE_WORKER, ROLE_CASHIER]

EXPENSE_CATEGORIES = [
    ("BIBIT", "Bibit"),
    ("PUPUK", "Pupuk"),
    ("PESTISIDA", "Pestisida"),
    ("TENAGA_KERJA", "Tenaga Kerja"),
    ("UTILITAS", "Utilitas"),
    ("PERALATAN", "Peralatan"),
    ("LAINNYA", "Lainnya"),
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
    "Masa henti pestisida (PHI) sudah terlewati",
    "Sampel buah memenuhi kematangan / brix target",
    "Alat panen, keranjang dan timbangan bersih",
    "Area sortir dan gudang siap",
    "Tenaga panen terjadwal",
]


def ensure_preharvest_checklist(PreHarvestChecklistItem) -> int:
    """Seed the default checklist only when none exist (admin may edit freely afterwards)."""
    if PreHarvestChecklistItem.objects.exists():
        return 0
    for i, text in enumerate(PREHARVEST_CHECKLIST, start=1):
        PreHarvestChecklistItem.objects.create(text=text, order=i * 10)
    return len(PREHARVEST_CHECKLIST)
