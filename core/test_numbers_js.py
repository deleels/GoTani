"""Runs core/static/core/numbers.js in Node with a stub page and checks typing, pasting and parsing.

Phase 7: a bad escape in the character filter once erased every digit typed into money and kg fields; this
test runs the real script so such a slip fails CI. Skipped when Node is not installed."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

NODE = shutil.which("node")
SCRIPT = Path(settings.BASE_DIR) / "core" / "static" / "core" / "numbers.js"

HARNESS = r"""
const fs = require('fs');
const listeners = {};
global.window = { GT_LANG: process.argv[2] };
global.document = {
  documentElement: { lang: process.argv[2] },
  addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
  querySelectorAll: () => [],
};
global.Event = function (t) { this.type = t; };
eval(fs.readFileSync(process.argv[1], 'utf8'));
const n = window.idNum;
function input(kind, text) {   // what the field shows after typing `text`
  const el = { value: text, dataset: { num: kind }, selectionStart: text.length,
               matches: (s) => s === 'input[data-num]', setSelectionRange() {} };
  listeners.input.forEach((f) => f({ target: el }));
  return el.value;
}
function paste(kind, text) {
  const el = { value: '', dataset: { num: kind }, matches: (s) => s === 'input[data-num]',
               dispatchEvent() {} };
  listeners.paste.forEach((f) => f({ target: el, clipboardData: { getData: () => text }, preventDefault() {} }));
  return el.value;
}
console.log(JSON.stringify({
  money: input('money', '3000000'), kg: input('kg', process.argv[2] === 'en' ? '1234.5' : '1234,5'),
  paste: paste('money', 'Rp 3.000'), pasteEn: paste('money', 'Rp3,000'),
  parse: n.parse(process.argv[2] === 'en' ? 'Rp1,500,000' : 'Rp1.500.000'), rp: window.gtRupiah(15000),
}));
"""


@skipUnless(NODE, "node is not installed")
class NumbersJsTests(SimpleTestCase):
    def run_js(self, lang):
        out = subprocess.run([NODE, "-e", HARNESS, str(SCRIPT), lang], capture_output=True, text=True, timeout=30,
                             check=True)
        return json.loads(out.stdout)

    def test_indonesian(self):
        r = self.run_js("id")
        self.assertEqual(r["money"], "3.000.000")
        self.assertEqual(r["kg"], "1.234,5")
        self.assertEqual(r["paste"], "3.000")
        self.assertEqual(r["parse"], 1500000)

    def test_english(self):
        r = self.run_js("en")
        self.assertEqual(r["money"], "3,000,000")
        self.assertEqual(r["kg"], "1,234.5")
        self.assertEqual(r["pasteEn"], "3,000")
        self.assertEqual(r["parse"], 1500000)
