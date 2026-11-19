from django.db import migrations

from farm.bootstrap import ensure_preharvest_checklist


def forwards(apps, schema_editor):
    ensure_preharvest_checklist(apps.get_model("farm", "PreHarvestChecklistItem"))


class Migration(migrations.Migration):
    dependencies = [("farm", "0004_p9_estimates_checklist_activity_extras")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
