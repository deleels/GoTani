from decimal import Decimal as D

from django.test import TestCase

from core.numbers import IDDecimalField, IDNumberInput, format_id_decimal, parse_id_decimal


class IndonesianNumberTests(TestCase):
    """D81: Indonesian input format, server side."""

    def test_parse(self):
        cases = {"1.500,25": D("1500.25"), "2,75": D("2.75"), "1.500.000": D("1500000"), "1.5": D("1.5"),
                 "0.333": D("0.333"), "Rp 15.000,5": D("15000.5"), "25000.00": D("25000.00")}
        for text, expected in cases.items():
            self.assertEqual(parse_id_decimal(text), expected, text)
        self.assertEqual(parse_id_decimal("15.000", "money"), D("15000"))  # whole-Rupiah field
        self.assertEqual(parse_id_decimal("1.500", "kg"), D("1.5"))  # machine number from the browser

    def test_format(self):
        self.assertEqual(format_id_decimal(D("1500.250")), "1.500,25")
        self.assertEqual(format_id_decimal(D("25000.00"), 0), "25.000")
        self.assertEqual(format_id_decimal(D("0.500")), "0,5")
        self.assertEqual(format_id_decimal(D("-1234567")), "-1.234.567")

    def test_field_and_widget(self):
        field = IDDecimalField(kind="money", max_digits=14, decimal_places=2)
        self.assertEqual(field.clean("15.000"), D("15000"))
        self.assertEqual(IDDecimalField(kind="kg", max_digits=10, decimal_places=3).clean("2,5"), D("2.5"))
        html = IDNumberInput("kg").render("q", "1500.25")
        self.assertIn('value="1.500,25"', html)
        self.assertIn('data-num="kg"', html)
        with self.assertRaises(Exception):
            field.clean("abc")
