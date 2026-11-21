"""D129: structured scouting and the Berat bell alert."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.notifications import build_notifications
from farm import services
from farm.access import get_roles
from farm.bootstrap import ROLE_OWNER, ROLE_WORKER
from farm.models import ActivityLog, CropCycle, FarmElement, Variety
from farm.tests import make_user


class ScoutingTests(TestCase):
    def setUp(self):
        el = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=10)
        self.cycle = CropCycle.objects.create(element=el, variety=Variety.objects.create(name="M", crop="M"),
                                              stage="VEGETATIVE", is_active=True,
                                              planting_start_date=timezone.localdate() - timedelta(days=30))

    def log(self, finding, severity="", notes="x", days_ago=0):
        return services.log_activity(self.cycle, "SCOUTING", finding=finding, severity=severity, notes=notes,
                                     logged_at=timezone.now() - timedelta(days=days_ago, minutes=1))

    def test_rules(self):
        self.assertEqual((self.log("NORMAL", notes="").finding, ""), ("NORMAL", ""))
        self.assertEqual(services.log_activity(self.cycle, "SCOUTING").finding, "NORMAL")
        with self.assertRaises(ValidationError):
            self.log("PEST")
        with self.assertRaises(ValidationError):
            self.log("PEST", "SEVERE", notes="")
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "IRRIGATION", finding="PEST")
        self.assertEqual(self.log("NORMAL", "SEVERE").severity, "")  # Normal drops the severity

    def severe_ids(self):
        return [c.pk for c, _ in services.severe_scouting_cycles()]

    def test_severe_alert_until_calmer_scouting(self):
        self.log("DISEASE", "SEVERE", days_ago=1)
        self.assertEqual(self.severe_ids(), [self.cycle.pk])
        self.log("DISEASE", "MEDIUM")
        self.assertEqual(self.severe_ids(), [self.cycle.pk])
        self.log("DISEASE", "LIGHT")
        self.assertEqual(self.severe_ids(), [])

    def test_old_severe_finding_expires_and_bell_roles(self):
        self.log("PEST", "SEVERE", days_ago=1)
        owner, worker = make_user("o", ROLE_OWNER), make_user("w", ROLE_WORKER)
        rf = self.client
        rf.force_login(owner)
        req = rf.get(reverse("home")).wsgi_request
        kinds = [n["kind"] for n in build_notifications(get_roles(req))]
        self.assertIn("scouting_severe", kinds)
        rf.force_login(worker)
        req = rf.get(reverse("home")).wsgi_request
        self.assertNotIn("scouting_severe", [n["kind"] for n in build_notifications(get_roles(req))])
        ActivityLog.objects.update(logged_at=timezone.now() - timedelta(days=4))
        self.assertEqual(self.severe_ids(), [])

    def test_form(self):
        self.client.force_login(make_user("w", ROLE_WORKER))
        url = reverse("farm:activities") + "?tab=scouting"
        r = self.client.post(url, {"cycle": self.cycle.pk, "finding": "PEST", "severity": "MEDIUM", "notes": "Thrips"})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(url), "Sedang")
        r = self.client.post(url, {"cycle": self.cycle.pk, "finding": "PEST", "severity": "", "notes": "Thrips"})
        self.assertEqual(r.status_code, 200)  # re-rendered with the error
