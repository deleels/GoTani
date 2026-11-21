"""Phase 7: every UI string has English, the compiled catalog is current, and the switch works."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from core import i18n
from core.services import default_language_for_role, get_user_profile, set_user_language
from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER


class CatalogTests(TestCase):
    def test_every_msgid_has_english(self):
        entries = i18n.read_po(i18n.po_path("en"))
        missing = [f"{src}: {k!r}" for k, src in i18n.extract_all().items() if not entries.get(k)]
        self.assertEqual(missing, [], "Run `python manage.py i18n` and add English to locale/en/tr_*.py:\n" + "\n".join(missing[:40]))

    def test_compiled_catalog_is_current(self):
        entries = i18n.read_po(i18n.po_path("en"))
        mo = i18n.po_path("en").with_suffix(".mo")
        self.assertEqual(mo.read_bytes(), i18n.compile_mo(entries), "Run `python manage.py i18n` to recompile django.mo.")

    def test_placeholders_match(self):
        import re

        entries = i18n.read_po(i18n.po_path("en"))
        bad = [k for k, v in entries.items() if v and sorted(re.findall(r"%\(\w+\)s", k)) != sorted(re.findall(r"%\(\w+\)s", v))]
        self.assertEqual(bad, [])


class LanguageSwitchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("own", password="x")
        self.user.groups.add(Group.objects.get_or_create(name=ROLE_OWNER)[0])
        self.client.force_login(self.user)

    def test_role_defaults(self):
        self.assertEqual(default_language_for_role(ROLE_OWNER), "en")
        self.assertEqual(default_language_for_role(ROLE_MANAGER), "en")
        self.assertEqual(default_language_for_role(ROLE_CASHIER), "id")

    def test_profile_language_drives_ui_and_numbers(self):
        self.assertContains(self.client.get(reverse("core:profile")), 'lang="id"')
        set_user_language(self.user, "en")
        r = self.client.get(reverse("core:profile"))
        self.assertContains(r, 'lang="en"')
        self.assertContains(r, "Change password")
        with translation.override("en"):
            from farm.templatetags.gotani import kg, rupiah

            self.assertEqual(rupiah(15000), "Rp15,000")
            self.assertEqual(kg("2.5"), "2.5")
        with translation.override("id"):
            self.assertEqual(rupiah(15000), "Rp15.000")

    def test_switch_view_saves_and_redirects_safely(self):
        r = self.client.post(reverse("core:set_language"), {"language": "en", "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(get_user_profile(self.user).language, "en")
        r = self.client.post(reverse("core:set_language"), {"language": "id", "next": reverse("core:profile")})
        self.assertRedirects(r, reverse("core:profile"), fetch_redirect_response=False)
        self.assertEqual(get_user_profile(self.user).language, "id")

    def test_browser_language_is_ignored(self):
        r = self.client.get(reverse("core:profile"), HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9")
        self.assertContains(r, 'lang="id"')


class FeedbackUiTests(TestCase):
    """Phase 7 step 2: messages are toasts, forced password change opens a notice dialog, forms load forms.js."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("own2", password="x")
        self.user.groups.add(Group.objects.get_or_create(name=ROLE_OWNER)[0])
        self.client.force_login(self.user)

    def test_messages_render_as_toasts(self):
        r = self.client.post(reverse("core:profile"), {"first_name": "Ani", "last_name": "", "language": "id"}, follow=True)
        self.assertContains(r, 'class="app-toast app-toast--success"')
        self.assertContains(r, "Profil disimpan.")
        self.assertContains(r, "core/forms.js")

    def test_forced_password_change_opens_notice_dialog(self):
        profile = get_user_profile(self.user)
        profile.must_change_password = True
        profile.save()
        r = self.client.get(reverse("core:password_change"))
        self.assertContains(r, 'id="forced-notice"')
        self.assertNotContains(r, 'href="%s" class="app-back"' % reverse("core:profile"))
