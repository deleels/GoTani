"""D131: one report for several cycles, all or nothing."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from farm import services
from farm.bootstrap import ROLE_WORKER
from farm.models import ActivityLog, CropCycle, Expense, FarmElement, InputItem, Variety
from farm.tests import make_user


class MultiActivityTests(TestCase):
    def setUp(self):
        v = Variety.objects.create(name="M", crop="M")
        self.cycles = []
        for n in ("A", "B", "C"):
            el = FarmElement.objects.create(name=n, element_type="GREENHOUSE", area_m2=10)
            self.cycles.append(CropCycle.objects.create(element=el, variety=v, stage="VEGETATIVE", is_active=True,
                                                        planting_start_date=timezone.localdate()))
        self.npk = InputItem.objects.create(name="NPK", item_type="FERTILIZER", unit="KG")
        services.record_input_purchase(self.npk, 10, 1000)

    def test_saves_each_row_with_usage_cost(self):
        acts = services.log_activities_multi("FERTILIZER", [(c, 2, None) for c in self.cycles],
                                             input_item=self.npk, unit="KG")
        self.assertEqual(len(acts), 3)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("4.000"))
        self.assertEqual(Expense.objects.filter(source="INPUT_USAGE").count(), 3)

    def test_short_stock_rejects_everything(self):
        with self.assertRaisesMessage(ValidationError, "kurang 2"):
            services.log_activities_multi("FERTILIZER", [(c, 4, None) for c in self.cycles],
                                          input_item=self.npk, unit="KG")
        self.assertEqual(ActivityLog.objects.count(), 0)

    def test_one_bad_row_rolls_back(self):
        self.cycles[2].is_active = False
        self.cycles[2].save()
        with self.assertRaises(ValidationError):
            services.log_activities_multi("IRRIGATION", [(c, 5, 10) for c in self.cycles], unit="L")
        self.assertEqual(ActivityLog.objects.count(), 0)
        with self.assertRaises(ValidationError):
            services.log_activities_multi("SCOUTING", [(self.cycles[0], 0, None)])
        with self.assertRaises(ValidationError):
            services.log_activities_multi("IRRIGATION", [(self.cycles[0], 1, None)] * 2, unit="L")

    def test_view(self):
        self.client.force_login(make_user("w", ROLE_WORKER))
        url = reverse("farm:activities_multi") + "?tab=lainnya"
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"tab": "lainnya", "task_name": "Penyiangan", "cycle_0": self.cycles[0].pk,
                                   "dur_0": "30", "cycle_1": self.cycles[1].pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ActivityLog.objects.filter(activity_type="OTHER").count(), 2)
        self.assertEqual(self.client.get(reverse("farm:activities_multi") + "?tab=scouting").status_code, 302)
        self.assertContains(self.client.get(reverse("farm:activities") + "?tab=irigasi"), "Beberapa siklus")
