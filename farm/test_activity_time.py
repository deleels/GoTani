"""D126: when the work was done (up to 3 days back)."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from farm import services
from farm.bootstrap import ROLE_WORKER
from farm.models import ActivityLog, CropCycle, FarmElement, Variety
from farm.tests import make_user


class ActivityTimeTests(TestCase):
    def setUp(self):
        self.worker = make_user("w", ROLE_WORKER)
        el = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=10)
        v = Variety.objects.create(name="Melon", crop="Melon")
        self.cycle = CropCycle.objects.create(element=el, variety=v, stage="VEGETATIVE", is_active=True,
                                              planting_start_date=timezone.localdate() - timedelta(days=30))

    def test_default_is_now_and_not_backdated(self):
        a = services.log_activity(self.cycle, "SCOUTING", user=self.worker)
        self.assertLess(abs((a.logged_at - timezone.now()).total_seconds()), 5)
        self.assertFalse(a.is_backdated)

    def test_backdate_within_three_days(self):
        when = timezone.now() - timedelta(days=2)
        a = services.log_activity(self.cycle, "SCOUTING", logged_at=when, user=self.worker)
        self.assertEqual(a.logged_at, when)
        self.assertTrue(a.is_backdated)

    def test_limits(self):
        for when in (timezone.now() + timedelta(hours=1), timezone.now() - timedelta(days=3, hours=1)):
            with self.subTest(when=when), self.assertRaises(ValidationError):
                services.log_activity(self.cycle, "SCOUTING", logged_at=when)
        self.cycle.planting_start_date = timezone.localdate()
        self.cycle.save()
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "SCOUTING", logged_at=timezone.now() - timedelta(days=2))

    def test_form_field_and_table(self):
        self.client.force_login(self.worker)
        when = timezone.localtime() - timedelta(days=1)
        r = self.client.post(reverse("farm:activities") + "?tab=scouting",
                             {"cycle": self.cycle.pk, "logged_at": when.strftime("%Y-%m-%dT%H:%M"), "notes": "ok", "finding": "NORMAL"})
        self.assertEqual(r.status_code, 302)
        a = ActivityLog.objects.get()
        self.assertEqual(timezone.localtime(a.logged_at).strftime("%Y-%m-%d %H:%M"), when.strftime("%Y-%m-%d %H:%M"))
        self.assertContains(self.client.get(reverse("farm:activities") + "?tab=scouting"), "dicatat")
