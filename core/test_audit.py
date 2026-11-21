from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core import services
from core.models import AuditLog
from farm.bootstrap import ROLE_OWNER, ensure_role_groups
from farm.models import Variety
from inventory import services as inventory
from inventory.models import Grade, Product


class AuditLogTests(TestCase):
    """D82: append-only audit log; price, kasir step, PKP and admin edits are recorded."""

    def setUp(self):
        from django.contrib.auth.models import Group as G
        ensure_role_groups(G)
        from django.contrib.auth.models import Group

        self.owner = get_user_model().objects.create_superuser("owner", password="x")
        self.owner.groups.add(Group.objects.get(name=ROLE_OWNER))
        self.product = Product.objects.create(variety=Variety.objects.create(name="Melon", crop="Melon"),
                                              grade=Grade.HIGH)

    def test_price_changes_are_logged(self):
        inventory.set_product_price(self.product, 20000, actor=self.owner)
        inventory.set_product_price(self.product, 20000, actor=self.owner)  # unchanged: no entry
        entries = list(AuditLog.objects.order_by("id"))
        self.assertEqual([e.action for e in entries], ["PRICE_CHANGE"])
        self.assertEqual(entries[0].before, {"selling_price_idr": None})
        self.assertEqual(entries[0].after, {"selling_price_idr": "20000.00"})
        self.assertEqual(entries[0].actor, self.owner)

    def test_pkp_switch_is_logged(self):
        services.update_farm_profile(self.owner, is_pkp=True)
        entry = AuditLog.objects.get()
        self.assertEqual((entry.action, entry.after), ("PKP_CHANGE", {"is_pkp": True}))

    def test_append_only(self):
        inventory.set_product_price(self.product, 1000, actor=self.owner)
        entry = AuditLog.objects.get()
        with self.assertRaises(ValueError):
            entry.save()
        with self.assertRaises(ValueError):
            entry.delete()

    def test_admin_edit_is_logged(self):
        from inventory.models import Tax

        tax = Tax.objects.create(name="PPN", rate_pct=11)
        self.client.force_login(self.owner)
        url = reverse("admin:inventory_product_change", args=[self.product.pk])
        self.client.post(url, {"variety": self.product.variety_id, "grade": "HIGH", "selling_price_idr": "15000",
                               "tax": tax.pk, "pos_step_kg": "0.5"})
        self.product.refresh_from_db()
        self.assertIsNone(self.product.selling_price_idr)  # price is read-only in admin (D123)
        entry = AuditLog.objects.get()
        self.assertEqual(entry.action, "PRODUCT_ADMIN_EDIT")
        self.assertEqual(entry.after, {"tax_id": tax.pk})

    def test_log_page_roles_and_content(self):
        from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER
        from django.contrib.auth.models import Group

        inventory.set_product_price(self.product, 20000, actor=self.owner)
        url = reverse("core:audit_log")
        self.client.force_login(self.owner)
        resp = self.client.get(url, {"action": "PRICE_CHANGE"})
        self.assertContains(resp, "Harga Melon")
        self.assertContains(resp, "- → 20.000")
        self.assertNotContains(self.client.get(url, {"q": "zzz"}), "Harga Melon")
        for role, status in ((ROLE_MANAGER, 200), (ROLE_CASHIER, 403)):
            user = get_user_model().objects.create_user(role.lower(), password="x")
            user.groups.add(Group.objects.get(name=role))
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, status, role)
