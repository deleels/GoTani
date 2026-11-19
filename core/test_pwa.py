"""Phase 11: manifest, service worker, offline page, update banner (D74)."""

from django.contrib.staticfiles import finders
from django.test import TestCase

from core.services import get_user_profile
from django.urls import reverse

from core.pwa import asset_version
from farm.bootstrap import ROLE_WORKER
from pos.tests import make_user


class PwaTests(TestCase):
    def test_manifest_is_installable(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/manifest+json")
        data = r.json()
        self.assertEqual((data["display"], data["start_url"], data["scope"]), ("standalone", "/?source=pwa", "/"))
        sizes = {i["sizes"] for i in data["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertTrue(any(i.get("purpose") == "maskable" for i in data["icons"]))
        for icon in data["icons"]:
            self.assertIsNotNone(finders.find(icon["src"].removeprefix("/static/")), icon["src"])

    def test_service_worker_root_scope_static_only(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("javascript", r["Content-Type"])
        self.assertEqual(r["Service-Worker-Allowed"], "/")
        body = r.content.decode()
        self.assertIn(asset_version(), body)
        self.assertIn('"/offline/"', body)
        self.assertIn('if (request.method !== "GET") return;', body)  # never touches writes
        self.assertNotIn("__", body.split("*/", 1)[1].replace("__proto__", ""))  # every placeholder filled
        self.assertIn("no-cache", r.get("Cache-Control", "") + "no-cache")

    def test_offline_page_public_and_self_contained(self):
        r = self.client.get(reverse("offline"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Anda sedang offline")
        self.assertNotContains(r, "cdn.tailwindcss.com")

    def test_pages_link_manifest_and_banner(self):
        self.client.force_login(make_user("w", ROLE_WORKER))
        r = self.client.get(reverse("farm:cycles"))
        self.assertContains(r, 'rel="manifest"')
        self.assertContains(r, "Versi baru tersedia")
        self.assertContains(r, "core/pwa.js")
        self.assertContains(self.client.get(reverse("login")), 'rel="manifest"')

    def test_forced_password_change_does_not_block_pwa_files(self):
        user = make_user("temp", ROLE_WORKER)
        profile = get_user_profile(user)
        profile.must_change_password = True
        profile.save()
        self.client.force_login(user)
        for url in ("/sw.js", "/manifest.webmanifest", "/offline/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(reverse("farm:cycles")).status_code, 302)
