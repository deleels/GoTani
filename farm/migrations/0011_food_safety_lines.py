"""D130: add the basic food-safety lines to an existing checklist (a new install gets them from bootstrap)."""

from django.db import migrations

LINES = [
    "Air untuk mencuci dan menyemprot bersih",
    "Toilet dan tempat cuci tangan tersedia untuk pekerja",
    "Tidak ada pekerja sakit yang ikut memanen",
    "Wadah panen bersih dan tidak dipakai untuk bahan kimia",
]


def add_lines(apps, schema_editor):
    Item = apps.get_model("farm", "PreHarvestChecklistItem")
    if not Item.objects.exists():
        return
    order = max(Item.objects.values_list("order", flat=True)) + 10
    for text in LINES:
        if not Item.objects.filter(text=text).exists():
            Item.objects.create(text=text, order=order)
            order += 10


class Migration(migrations.Migration):
    dependencies = [("farm", "0010_preharvest_answers")]
    operations = [migrations.RunPython(add_lines, migrations.RunPython.noop)]
