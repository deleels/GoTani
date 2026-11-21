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


class PurchasePaymentTests(TestCase):
    """D133: payment method and receipt on input purchases."""

    def test_purchase_form_saves_method_and_receipt(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from farm.models import InputPurchase

        item = InputItem.objects.create(name="NPK", item_type="FERTILIZER", unit="KG")
        self.client.force_login(make_user("o", ROLE_OWNER))
        r = self.client.post(reverse("farm:inputs"), {
            "item": item.pk, "qty": "5", "cost_per_unit_idr": "1000", "supplier": "Toko Tani",
            "payment_method": "QRIS", "receipt": SimpleUploadedFile("nota.pdf", b"%PDF-1.4", "application/pdf")})
        self.assertEqual(r.status_code, 302)
        p = InputPurchase.objects.get()
        self.assertEqual(p.payment_method, "QRIS")
        self.assertTrue(p.receipt.name.endswith(".pdf"))
        self.assertContains(self.client.get(reverse("farm:inputs")), "QRIS")
        p.receipt.delete(save=False)


class FirstHarvestSkewTests(TestCase):
    """D135: warning only; COGS unchanged."""

    def test_skew_rules(self):
        from decimal import Decimal as D

        from farm.models import HarvestBatch

        el = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=10)
        cycle = CropCycle.objects.create(element=el, variety=Variety.objects.create(name="M", crop="M"),
                                         stage="HARVESTING", is_active=True, planting_start_date=timezone.localdate())
        self.assertFalse(services.first_harvest_skewed(cycle))

        def batch(cost):
            return HarvestBatch.objects.create(cycle=cycle, high_quality_kg=10, low_quality_kg=0, harvest_loss_kg=0,
                                               absorbed_cost_idr=D(cost), unit_cogs_high_idr=0, unit_cogs_low_idr=0,
                                               harvested_at=timezone.now())
        batch(1000)
        self.assertTrue(services.first_harvest_skewed(cycle))
        batch(500)
        self.assertTrue(services.first_harvest_skewed(cycle))  # 1000 of 1500 > 60%
        batch(500)
        self.assertFalse(services.first_harvest_skewed(cycle))  # 1000 of 2000 = 50%
        self.assertEqual(services.first_harvest_skewed_varieties(), set())


class UxBundleTests(TestCase):
    """D136: variety notes, planted unit, Catat Panen card, report search, Telusuri button."""

    def test_cycle_page_and_search(self):
        owner = make_user("o", ROLE_OWNER)
        v = services.save_variety(None, name="Melon", crop="Melon", notes="Jarak tanam 50 cm")
        el = FarmElement.objects.create(name="GH Timur", element_type="GREENHOUSE", area_m2=10)
        cycle = services.start_cycle(el, v, timezone.localdate(), planted_qty=40, planted_unit="POLYBAG")
        self.client.force_login(owner)
        page = self.client.get(reverse("farm:cycle_detail", args=[cycle.pk]))
        self.assertContains(page, "Jarak tanam 50 cm")
        self.assertContains(page, "polybag")
        self.assertNotContains(page, "Catat Panen")
        cycle.stage = "HARVESTING"
        cycle.save()
        self.assertContains(self.client.get(reverse("farm:cycle_detail", args=[cycle.pk])), "Catat Panen")
        services.log_activity(cycle, "OTHER", task_name="Penyiangan")
        services.log_activity(cycle, "OTHER", task_name="Pemangkasan")
        url = reverse("farm:activities")
        r = self.client.get(url, {"tab": "lainnya", "q": "mangkas"})
        self.assertEqual([a.task_name for a in r.context["activities"]], ["Pemangkasan"])
        self.assertContains(r, reverse("reports:trace_index"))
