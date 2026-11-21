"""Phase 7: existing OWNER/MANAGER accounts (and superusers) start in English; everyone else keeps Indonesian."""

from django.conf import settings
from django.db import migrations
from django.db.models import Q


def forwards(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    UserProfile = apps.get_model("core", "UserProfile")
    leads = User.objects.filter(Q(is_superuser=True) | Q(groups__name__in=["OWNER", "MANAGER"])).distinct()
    for user in leads:
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.language:
            profile.language = "en"
            profile.save(update_fields=["language"])


class Migration(migrations.Migration):
    dependencies = [("core", "0004_language"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
