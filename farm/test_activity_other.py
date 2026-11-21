"""D127: the Lainnya (OTHER) work type."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from farm import services
from farm.bootstrap import ROLE_WORKER
from farm.models import ActivityLog, CropCycle, FarmElement, InputItem, Variety
from farm.tests import make_user


class OtherActivityTests(TestCase):
    def setUp(self):
        self.worker = make_user("w", ROLE_WORKER)
        el = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=10)
        v = Variety.objects.create(name="Melon", crop="Melon")
        self.cycle = CropCycle.objects.create(element=el, variety=v, stage="VEGETATIVE", is_active=True,
                                              planting_start_date=timezone.localdate() - timedelta(days=30))

    def test_logs_task_with_workers_duration_photo_free(self):
        a = services.log_activity(self.cycle, "OTHER", task_name="Penyiangan", duration_minutes=90, worker_count=3)
        self.assertEqual((a.task_name, a.duration_minutes, a.worker_count, a.input_item), ("Penyiangan", 90, 3, None))

    def test_rules(self):
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "OTHER")
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "OTHER", task_name="X", worker_count=0)
        item = InputItem.objects.create(name="NPK", item_type="FERTILIZER", unit="KG")
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "OTHER", task_name="X", input_item=item, quantity=1, unit="KG")
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "SCOUTING", task_name="X")
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "FERTILIZER", duration_minutes=10)

    def test_allowed_on_recently_cleared_cycle_only(self):
        self.cycle.stage, self.cycle.is_active, self.cycle.closed_at = "CLEARING", False, timezone.now() - timedelta(days=1)
        self.cycle.save()
        services.log_activity(self.cycle, "OTHER", task_name="Bongkar mulsa")
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "SCOUTING")
        self.cycle.closed_at = timezone.now() - timedelta(days=5)
        self.cycle.save()
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "OTHER", task_name="Bongkar mulsa")

    def test_tab_and_form(self):
        self.client.force_login(self.worker)
        url = reverse("farm:activities") + "?tab=lainnya"
        r = self.client.post(url, {"cycle": self.cycle.pk, "task_name": "Pemangkasan", "worker_count": "2",
                                   "duration_minutes": "", "notes": ""})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ActivityLog.objects.get().activity_type, "OTHER")
        self.assertContains(self.client.get(url), "Pemangkasan")


class ReportPickerTests(TestCase):
    """D128: grouped picker, preset to the cycle on the cycle page."""

    def test_groups_and_cycle_preset(self):
        from farm.views import report_picker

        groups = report_picker(7)
        self.assertEqual([str(g["label"]) for g in groups], ["Perawatan", "Pemantauan", "Panen & Lainnya"])
        urls = [it["url"] for g in groups for it in g["items"]]
        self.assertEqual(len(urls), 9)
        self.assertTrue(all("cycle=7" in u for u in urls if "spoilage" not in u))

    def test_pages_render_picker(self):
        worker = make_user("w", ROLE_WORKER)
        el = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=10)
        cycle = CropCycle.objects.create(element=el, variety=Variety.objects.create(name="M", crop="M"),
                                         stage="VEGETATIVE", is_active=True, planting_start_date=timezone.localdate())
        self.client.force_login(worker)
        self.assertContains(self.client.get(reverse("farm:activities")), "Pemantauan")
        r = self.client.get(reverse("farm:cycle_detail", args=[cycle.pk]))
        self.assertContains(r, f"tab=lainnya&amp;new=1&amp;cycle={cycle.pk}")
        r = self.client.get(reverse("farm:activities") + f"?tab=lainnya&new=1&cycle={cycle.pk}")
        self.assertEqual(str(r.context["form"]["cycle"].value()), str(cycle.pk))
