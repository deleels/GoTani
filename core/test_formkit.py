"""D121: select prompts, units beside the number, password eye."""

from django.template import Context, Template
from django.test import TestCase
from django.utils import translation

from farm.forms import ActivityForm, ExpenseForm, HarvestForm, PurchaseForm
from farm.models import ActivityLog, InputItem


class SelectPromptTests(TestCase):
    def test_required_select_starts_on_a_named_prompt(self):
        with translation.override("id"):
            html = str(HarvestForm()["cycle"])
        self.assertIn('<option value="" selected>Pilih siklus…</option>', html)

    def test_prompt_follows_the_language(self):
        with translation.override("en"):
            html = str(HarvestForm()["cycle"])
        self.assertIn("Choose a cycle…", html)

    def test_optional_select_offers_none(self):
        with translation.override("id"):
            html = str(ExpenseForm()["cycle"])
        self.assertIn(">Tidak ada</option>", html)

    def test_prompt_follows_required_set_after_init(self):
        with translation.override("id"):
            seeding = ActivityForm(activity_type=ActivityLog.ActivityType.SEEDING)
            fert = ActivityForm(activity_type=ActivityLog.ActivityType.FERTILIZER)
        self.assertEqual(seeding.fields["input_item"].empty_label, "Pilih input…")
        self.assertEqual(fert.fields["input_item"].empty_label, "Tidak ada")


class UnitTests(TestCase):
    def test_kg_field_has_suffix(self):
        html = str(HarvestForm()["high_quality_kg"])
        self.assertIn('class="app-unit" data-unit="kg"', html)

    def test_irrigation_volume_joins_unit_select(self):
        with translation.override("en"):
            form = ActivityForm(activity_type=ActivityLog.ActivityType.IRRIGATION)
            html = Template('{% include "_form_fields.html" %}').render(Context({"form": form}))
        self.assertEqual(form.fields["quantity"].label, "Water volume")
        self.assertIn('class="app-joined"', html)
        self.assertEqual(html.count('name="unit"'), 1)  # drawn once, beside the number
        self.assertIn('data-unit="min"', html)

    def test_purchase_quantity_follows_item_unit(self):
        item = InputItem.objects.create(name="Urea", item_type=InputItem.ItemType.FERTILIZER, unit="KG",
                                        stock_qty=0, avg_cost_per_unit_idr=0, min_alert_qty=0)
        html = str(PurchaseForm()["qty"])
        self.assertIn('data-unit-from="id_item"', html)
        self.assertIn(f'&quot;{item.pk}&quot;: &quot;kg&quot;', html)


class PasswordToggleTests(TestCase):
    def test_password_fields_get_the_eye(self):
        html = self.client.get("/accounts/login/").content.decode()
        self.assertIn("data-pw-toggle", html)
        self.assertIn('aria-pressed="false"', html)
