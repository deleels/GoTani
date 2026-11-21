"""D123: transaction data is view-only in the Django admin."""

from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.test import RequestFactory, TestCase

from farm.models import ActivityLog, CropCycle, Expense, HarvestBatch, InputLedger, InputPurchase
from inventory.models import Product, SpoilageRecord, StockLedger, StockLot
from invoicing.models import Invoice, InvoiceLineLotAllocation, InvoicePayment, PaymentAllocation
from pos.models import LineLotAllocation, OrderLineItem, SaleOrder

VIEW_ONLY = [
    ActivityLog, Expense, HarvestBatch, InputLedger, InputPurchase,
    SpoilageRecord, StockLedger, Invoice, InvoiceLineLotAllocation, InvoicePayment, PaymentAllocation,
    User, Group,
]
NO_ADD_NO_DELETE = [StockLot, SaleOrder, OrderLineItem, LineLotAllocation]


class AdminLockdownTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = User.objects.create_superuser("root", password="x")

    def test_transaction_models_are_view_only(self):
        for model in VIEW_ONLY:
            ma = admin.site._registry[model]
            with self.subTest(model=model.__name__):
                self.assertFalse(ma.has_add_permission(self.request))
                self.assertFalse(ma.has_change_permission(self.request))
                self.assertFalse(ma.has_delete_permission(self.request))
                self.assertTrue(ma.has_view_permission(self.request))

    def test_sales_and_lots_cannot_be_added_or_deleted(self):
        for model in NO_ADD_NO_DELETE:
            ma = admin.site._registry[model]
            with self.subTest(model=model.__name__):
                self.assertFalse(ma.has_add_permission(self.request))
                self.assertFalse(ma.has_delete_permission(self.request))

    def test_service_owned_fields_are_read_only(self):
        cycle_ma = admin.site._registry[CropCycle]
        self.assertIn("stage", cycle_ma.get_readonly_fields(self.request))
        product_ma = admin.site._registry[Product]
        self.assertIn("selling_price_idr", product_ma.get_readonly_fields(self.request))
        self.assertNotIn("selling_price_idr", product_ma.list_editable)

    def test_every_inline_of_a_view_only_model_is_locked(self):
        for model in VIEW_ONLY:
            ma = admin.site._registry[model]
            for inline_cls in ma.inlines:
                inline = inline_cls(model, admin.site)
                with self.subTest(inline=inline_cls.__name__):
                    self.assertFalse(inline.has_add_permission(self.request, None))
                    self.assertFalse(inline.has_change_permission(self.request, None))
