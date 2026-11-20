"""Phase 7: farm profile, staff management, forced password change, shell/nav, pagination."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core import services
from core.models import FarmProfile, UserProfile
from core.pagination import paginate
from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, ROLE_WORKER

STRONG = "Kebun#Melon2026"


def make_user(username, role=None, **kw):
    user = get_user_model().objects.create_user(username=username, password=STRONG, **kw)
    if role:
        user.groups.add(Group.objects.get(name=role))
    return user


class Base(TestCase):
    def setUp(self):
        self.owner = make_user("owner", ROLE_OWNER)
        self.manager = make_user("manager", ROLE_MANAGER)
        self.worker = make_user("worker", ROLE_WORKER)
        self.cashier = make_user("cashier", ROLE_CASHIER)


class FarmProfileTests(Base):
    def test_singleton_get_or_create(self):
        a = services.get_farm_profile()
        b = services.get_farm_profile()
        self.assertEqual(a.pk, 1)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(FarmProfile.objects.count(), 1)

    def test_second_row_rejected_by_db(self):
        services.get_farm_profile()
        with self.assertRaises(IntegrityError), transaction.atomic():
            FarmProfile.objects.create(pk=2, name="Kebun lain")

    def test_owner_updates_coordinates(self):
        p = services.update_farm_profile(self.owner, name="Kebun Bogor", latitude=Decimal("-6.595"),
                                         longitude=Decimal("106.816"))
        self.assertTrue(p.has_location)
        self.assertEqual(p.name, "Kebun Bogor")

    def test_non_owner_cannot_update(self):
        with self.assertRaises(PermissionDenied):
            services.update_farm_profile(self.manager, name="X")

    def test_half_coordinates_rejected(self):
        with self.assertRaises(ValidationError):
            services.update_farm_profile(self.owner, latitude=Decimal("-6.5"), longitude=None)

    def test_out_of_range_coordinates_rejected(self):
        with self.assertRaises(ValidationError):
            services.update_farm_profile(self.owner, latitude=Decimal("95"), longitude=Decimal("10"))


class StaffServiceTests(Base):
    def test_owner_creates_user_with_forced_change(self):
        u = services.create_staff_user(self.owner, username="budi", full_name="Budi Santoso",
                                       role=ROLE_WORKER, temp_password=STRONG)
        self.assertEqual(services.user_role(u), ROLE_WORKER)
        self.assertEqual((u.first_name, u.last_name), ("Budi", "Santoso"))
        self.assertTrue(u.profile.must_change_password)
        self.assertTrue(u.check_password(STRONG))

    def test_only_owner_creates(self):
        for actor in (self.manager, self.worker, self.cashier):
            with self.assertRaises(PermissionDenied):
                services.create_staff_user(actor, username="x", full_name="", role=ROLE_WORKER, temp_password=STRONG)

    def test_duplicate_username_case_insensitive(self):
        with self.assertRaises(ValidationError):
            services.create_staff_user(self.owner, username="WORKER", full_name="", role=ROLE_WORKER,
                                       temp_password=STRONG)

    def test_weak_password_and_unknown_role_rejected(self):
        with self.assertRaises(ValidationError):
            services.create_staff_user(self.owner, username="a", full_name="", role=ROLE_WORKER, temp_password="123")
        with self.assertRaises(ValidationError):
            services.create_staff_user(self.owner, username="b", full_name="", role="BOSS", temp_password=STRONG)

    def test_change_role(self):
        services.set_staff_role(self.owner, self.worker, ROLE_CASHIER)
        self.assertEqual(services.user_role(self.worker), ROLE_CASHIER)

    def test_last_owner_cannot_be_demoted_or_deactivated(self):
        with self.assertRaises(ValidationError):
            services.set_staff_role(self.owner, self.owner, ROLE_MANAGER)
        second = make_user("owner2", ROLE_OWNER)
        with self.assertRaises(ValidationError):  # cannot deactivate yourself
            services.set_staff_active(self.owner, self.owner, False)
        services.set_staff_active(self.owner, second, False)
        with self.assertRaises(ValidationError):
            services.set_staff_role(self.owner, self.owner, ROLE_MANAGER)

    def test_demote_owner_allowed_when_another_owner_active(self):
        second = make_user("owner2", ROLE_OWNER)
        services.set_staff_role(self.owner, second, ROLE_MANAGER)
        self.assertEqual(services.user_role(second), ROLE_MANAGER)

    def test_deactivate_and_reactivate(self):
        services.set_staff_active(self.owner, self.worker, False)
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_active)
        services.set_staff_active(self.owner, self.worker, True)
        self.worker.refresh_from_db()
        self.assertTrue(self.worker.is_active)

    def test_reset_password_sets_flag(self):
        services.reset_staff_password(self.owner, self.worker, "Baru#Cabai2026")
        self.worker.refresh_from_db()
        self.assertTrue(self.worker.check_password("Baru#Cabai2026"))
        self.assertTrue(UserProfile.objects.get(user=self.worker).must_change_password)
        services.mark_password_changed(self.worker)
        self.assertFalse(UserProfile.objects.get(user=self.worker).must_change_password)


class ForcedPasswordChangeTests(Base):
    def test_flagged_user_is_redirected_until_changed(self):
        services.reset_staff_password(self.owner, self.worker, STRONG)
        self.client.force_login(self.worker)
        resp = self.client.get(reverse("farm:cycles"))
        self.assertRedirects(resp, reverse("core:password_change"), fetch_redirect_response=False)
        resp = self.client.post(reverse("core:password_change"), {
            "old_password": STRONG, "new_password1": "Sandi#Baru2026x", "new_password2": "Sandi#Baru2026x"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(UserProfile.objects.get(user=self.worker).must_change_password)
        self.assertEqual(self.client.get(reverse("farm:cycles")).status_code, 200)

    def test_logout_allowed_while_flagged(self):
        services.reset_staff_password(self.owner, self.worker, STRONG)
        self.client.force_login(self.worker)
        resp = self.client.post(reverse("logout"))
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp["Location"], reverse("core:password_change"))


class StaffViewTests(Base):
    def test_access_is_owner_only(self):
        for url in (reverse("core:staff"), reverse("core:settings")):
            for user in (self.manager, self.worker, self.cashier):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, 403)
            self.client.force_login(self.owner)
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_create_via_sheet_and_errors_reopen_it(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("core:staff"), {"username": "sari", "full_name": "Sari", "role": ROLE_CASHIER,
                                                        "temp_password": STRONG})
        self.assertRedirects(resp, reverse("core:staff"))
        self.assertTrue(get_user_model().objects.filter(username="sari").exists())
        resp = self.client.post(reverse("core:staff"), {"username": "sari", "role": ROLE_CASHIER,
                                                        "temp_password": STRONG})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.context["sheet_open"])

    def test_actions_and_search(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("core:staff_action", args=[self.worker.pk, "deactivate"]))
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_active)
        resp = self.client.get(reverse("core:staff") + "?tab=inactive")
        self.assertContains(resp, "@worker")
        resp = self.client.get(reverse("core:staff") + "?q=cash")
        self.assertContains(resp, "@cashier")
        self.assertNotContains(resp, "@manager")
        resp = self.client.post(reverse("core:staff_action", args=[self.owner.pk, "deactivate"]), follow=True)
        self.assertContains(resp, "sendiri")

    def test_settings_saves_profile(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("core:settings"), {"name": "Kebun Ciawi", "address": "", "phone": "",
                                                           "latitude": "-6.6", "longitude": "106.8"})
        self.assertRedirects(resp, reverse("core:settings"))
        self.assertEqual(services.get_farm_profile().name, "Kebun Ciawi")
        self.assertFalse(services.farm_is_pkp())  # D80: off by default, unchecked box stays off

    def test_settings_toggles_pkp(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("core:settings"), {"name": "Kebun", "address": "", "phone": "",
                                                    "latitude": "", "longitude": "", "is_pkp": "on"})
        self.assertTrue(services.farm_is_pkp())


class ShellTests(Base):
    def test_sidebar_is_role_filtered(self):
        self.client.force_login(self.cashier)
        resp = self.client.get(reverse("pos:register"))
        self.assertContains(resp, "Kasir (POS)")
        self.assertNotContains(resp, "Analitik")
        self.assertNotContains(resp, "Laporan Kerja")
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("dashboard"))
        for label in ("Analitik", "Staf", "Pengaturan", "Laporan Kerja", "Telusuri"):
            self.assertContains(resp, label)
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse("dashboard")), reverse("core:staff"))

    def test_login_page_has_no_signup(self):
        resp = self.client.get(reverse("login"))
        self.assertContains(resp, "Selamat datang kembali")
        self.assertNotContains(resp, "Daftar")


class PaginationTests(TestCase):
    def test_page_size_and_querystring(self):
        rf = RequestFactory()
        page = paginate(rf.get("/x", {"per": "25", "q": "a", "page": "2"}), list(range(60)))
        self.assertEqual((page.per, page.number, len(page.object_list)), (25, 2, 25))
        self.assertIn("q=a", page.querystring)
        self.assertNotIn("page=", page.querystring)
        page = paginate(rf.get("/x", {"per": "999", "page": "abc"}), list(range(5)))
        self.assertEqual((page.per, page.number), (10, 1))


class ProfilePageTests(TestCase):
    def test_edit_own_name_any_role(self):
        from django.contrib.auth.models import Group

        from farm.bootstrap import ROLE_CASHIER

        user = get_user_model().objects.create_user("kasir1", password="x")
        user.groups.add(Group.objects.get_or_create(name=ROLE_CASHIER)[0])
        self.client.force_login(user)
        url = reverse("core:profile")
        self.assertContains(self.client.get(url), "CASHIER")
        self.assertRedirects(self.client.post(url, {"first_name": "Siti", "last_name": "Aminah", "language": "id"}), url)
        user.refresh_from_db()
        self.assertEqual(user.get_full_name(), "Siti Aminah")
        self.assertEqual(self.client.post(url, {"first_name": "  "}).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
