"""Seed ExpenseCategory rows (D37) and the four role Groups (D27). Idempotent."""

from django.db import migrations

from farm.bootstrap import EXPENSE_CATEGORIES, ROLES, ensure_expense_categories, ensure_role_groups


def seed(apps, schema_editor):
    ensure_expense_categories(apps.get_model("farm", "ExpenseCategory"))
    ensure_role_groups(apps.get_model("auth", "Group"))


def unseed(apps, schema_editor):
    ExpenseCategory = apps.get_model("farm", "ExpenseCategory")
    Group = apps.get_model("auth", "Group")
    # Leave categories that are already referenced by expenses (PROTECT).
    ExpenseCategory.objects.filter(
        code__in=[code for code, _ in EXPENSE_CATEGORIES], expenses__isnull=True
    ).delete()
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("farm", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
