"""D124: input items, varieties and element edits made in the app."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import AuditLog
from farm import services
from farm.bootstrap import ROLE_OWNER, ROLE_WORKER
from farm.tests import make_user
from farm.models import CropCycle, FarmElement, InputItem, Variety


class InAppSetupTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner", ROLE_OWNER)
        self.worker = make_user("worker", ROLE_WORKER)
        self.el = FarmElement.objects.create(name="GH 1", element_type="GREENHOUSE", area_m2=100)
        self.variety = Variety.objects.create(name="Melon Golden", crop="Melon", days_to_harvest=70)

    def test_create_input_item_with_first_purchase(self):
        item = services.create_input_item(name="NPK", item_type="FERTILIZER", unit="KG", min_alert_qty=5,
                                          first_qty=10, first_cost_per_unit_idr=12000, user=self.owner)
        self.assertEqual((item.stock_qty, item.avg_cost_per_unit_idr), (D("10.000"), D("12000.00")))

    def test_create_input_item_without_purchase_and_rules(self):
        item = services.create_input_item(name="Benih", item_type="SEED", unit="G")
        self.assertEqual(item.stock_qty, 0)
        with self.assertRaises(ValidationError):
            services.create_input_item(name="benih", item_type="SEED", unit="G")
        with self.assertRaises(ValidationError):
            services.create_input_item(name="X", item_type="SEED", unit="G", first_qty=1)

    def test_input_create_view_roles(self):
        url = reverse("farm:input_create")
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        r = self.client.post(url, {"name": "Fungisida", "item_type": "PESTICIDE", "unit": "mL", "min_alert_qty": "",
                                   "first_qty": "", "first_cost_per_unit_idr": "", "supplier": ""})
        self.assertRedirects(r, reverse("farm:inputs"), fetch_redirect_response=False)
        self.assertTrue(InputItem.objects.filter(name="Fungisida").exists())

    def test_variety_create_and_edit_audited(self):
        self.client.force_login(self.owner)
        r = self.client.post(reverse("farm:variety_create"), {"name": "Cabai Rawit", "crop": "Cabai",
                                                              "days_to_harvest": "90", "kc": "1,05",
                                                              "next": reverse("core:settings")})
        self.assertRedirects(r, reverse("core:settings"), fetch_redirect_response=False)
        v = Variety.objects.get(name="Cabai Rawit")
        self.assertEqual(v.kc, D("1.05"))
        services.save_variety(v, name="Cabai Rawit", crop="Cabai", days_to_harvest=95, kc=v.kc, user=self.owner)
        self.assertEqual(AuditLog.objects.get().action, "VARIETY_EDIT")
        with self.assertRaises(ValidationError):
            services.save_variety(None, name="melon golden", crop="Melon")
        self.assertContains(self.client.get(reverse("core:settings")), "Cabai Rawit")

    def test_element_edit_rules(self):
        services.update_element(self.el, name="GH Utama", area_m2=D("120"), element_type="SCREEN_HOUSE", user=self.owner)
        self.el.refresh_from_db()
        self.assertEqual((self.el.name, self.el.area_m2, self.el.element_type), ("GH Utama", D("120.00"), "SCREEN_HOUSE"))
        self.assertEqual(AuditLog.objects.get().action, "ELEMENT_EDIT")
        CropCycle.objects.create(element=self.el, variety=self.variety, stage="PLANTING",
                                 planting_start_date="2026-11-01", is_active=True)
        with self.assertRaises(ValidationError):
            services.update_element(self.el, name="GH Utama", area_m2=120, element_type="OPEN_FIELD")
        services.update_element(self.el, name="GH Utama", area_m2=150, element_type="SCREEN_HOUSE")
        FarmElement.objects.create(name="Lahan", element_type="OPEN_FIELD", area_m2=10)
        with self.assertRaises(ValidationError):
            services.update_element(self.el, name="lahan", area_m2=150, element_type="SCREEN_HOUSE")

    def test_element_edit_view(self):
        url = reverse("farm:element_edit", args=[self.el.pk])
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), "GH 1")
        r = self.client.post(url, {"name": "GH 1", "element_type": "GREENHOUSE", "area_m2": "250"})
        self.assertRedirects(r, reverse("farm:element_detail", args=[self.el.pk]), fetch_redirect_response=False)
        self.el.refresh_from_db()
        self.assertEqual(self.el.area_m2, D("250.00"))
