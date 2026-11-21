"""D132: limited editing of reports, audited."""

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog
from farm import services
from farm.bootstrap import ROLE_OWNER, ROLE_WORKER
from farm.models import CropCycle, FarmElement, InputItem, Variety
from farm.tests import make_user


class ActivityEditTests(TestCase):
    def setUp(self):
        el = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=10)
        self.cycle = CropCycle.objects.create(element=el, variety=Variety.objects.create(name="M", crop="M"),
                                              stage="VEGETATIVE", is_active=True, planting_start_date=timezone.localdate())
        self.owner = make_user("o", ROLE_OWNER)

    def test_edits_notes_and_method_only(self):
        item = InputItem.objects.create(name="NPK", item_type="FERTILIZER", unit="KG")
        services.record_input_purchase(item, 5, 1000)
        a = services.log_activity(self.cycle, "FERTILIZER", 2, "KG", item)
        services.update_activity_details(a, user=self.owner, application_method="Kocor", notes="baris 1")
        a.refresh_from_db()
        self.assertEqual((a.application_method, a.notes, a.quantity), ("Kocor", "baris 1", 2))
        self.assertEqual(AuditLog.objects.get().action, "ACTIVITY_EDIT")
        with self.assertRaises(ValidationError):
            services.update_activity_details(a, quantity=3)

    def test_scouting_rules_still_apply(self):
        a = services.log_activity(self.cycle, "SCOUTING", finding="PEST", severity="LIGHT", notes="kutu")
        with self.assertRaises(ValidationError):
            services.update_activity_details(a, finding="PEST", severity="", notes="kutu")
        services.update_activity_details(a, finding="PEST", severity="SEVERE", notes="kutu menyebar")
        a.refresh_from_db()
        self.assertEqual(a.severity, "SEVERE")

    def test_view_roles(self):
        a = services.log_activity(self.cycle, "OTHER", task_name="Siang")
        url = reverse("farm:activity_edit", args=[a.pk])
        self.client.force_login(make_user("w", ROLE_WORKER))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(url), 'name="application_method"')
        r = self.client.post(url, {"task_name": "Penyiangan", "notes": ""})
        self.assertRedirects(r, reverse("farm:activities") + "?tab=lainnya", fetch_redirect_response=False)
        a.refresh_from_db()
        self.assertEqual(a.task_name, "Penyiangan")
