"""Translation tooling without GNU gettext (Phase 7).

Indonesian is the source text (msgid); English lives in `locale/en/LC_MESSAGES/django.po`.
`python manage.py i18n` extracts every msgid from templates and Python, merges new ones into the
.po (empty msgstr), and compiles the .mo. `core.test_i18n` fails if any msgid has no English.

Recognised in templates: {% translate "…" %} / {% trans %}, {% blocktranslate %}…{% endblocktranslate %}
(msgid built like Django does: `%(var)s` for variables, `%%` for a literal %), and _("…") arguments.
Recognised in Python: _("…") / gettext("…") / gettext_lazy("…") / ngettext is not used.
"""

import ast
import re
import struct
from array import array
from pathlib import Path

from django.conf import settings
from django.template.base import Lexer, TokenType

PO_HEADER = (
    "Content-Type: text/plain; charset=UTF-8\n"
    "Content-Transfer-Encoding: 8bit\n"
    "Language: en\n"
    "Plural-Forms: nplurals=2; plural=(n != 1);\n"
)

_TRANSLATE = re.compile(r"""^(?:translate|trans|jt)\s+(?P<q>["'])(?P<s>.*?)(?P=q)(?:\s|$)""", re.S)
_UNDERSCORE = re.compile(r"""_\((?P<q>["'])(?P<s>.*?)(?P=q)\)""", re.S)
_BLOCK_START = re.compile(r"^(?:blocktranslate|blocktrans)\b(?P<rest>.*)$", re.S)


def template_dirs():
    base = Path(settings.BASE_DIR)
    return [base / "templates"]


def python_files():
    base = Path(settings.BASE_DIR)
    skip = {".venv", "migrations", "staticfiles", "media", "node_modules", "__pycache__"}
    for path in base.rglob("*.py"):
        parts = set(path.relative_to(base).parts)
        if parts & skip or path.name.startswith("test") or path.parent.name == "tests":
            continue
        yield path


def extract_template(text):
    found = []
    tokens = Lexer(text).tokenize()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.token_type == TokenType.BLOCK:
            contents = tok.contents.strip()
            m = _TRANSLATE.match(contents)
            if m:
                found.append(m.group("s"))
            bm = _BLOCK_START.match(contents)
            if bm:
                trimmed = " trimmed" in f" {bm.group('rest')} "
                parts = []
                i += 1
                while i < len(tokens):
                    t = tokens[i]
                    if t.token_type == TokenType.BLOCK and t.contents.strip() in ("endblocktranslate", "endblocktrans"):
                        break
                    if t.token_type == TokenType.TEXT:
                        parts.append(t.contents.replace("%", "%%"))
                    elif t.token_type == TokenType.VAR:
                        parts.append("%%(%s)s" % t.contents.strip())
                    i += 1
                msg = "".join(parts)
                if trimmed:
                    msg = " ".join(line.strip() for line in msg.strip().splitlines() if line.strip())
                found.append(msg)
            else:
                found.extend(m.group("s") for m in _UNDERSCORE.finditer(contents))
        elif tok.token_type == TokenType.VAR:
            found.extend(m.group("s") for m in _UNDERSCORE.finditer(tok.contents))
        i += 1
    return found


_PY_FUNCS = {"_", "gettext", "gettext_lazy", "_l"}


def extract_python(text):
    found = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _PY_FUNCS
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            found.append(node.args[0].value)
    return found


def extract_all():
    msgids = {}
    for d in template_dirs():
        for path in sorted(d.rglob("*.html")):
            for s in extract_template(path.read_text(encoding="utf-8")):
                msgids.setdefault(s, str(path.relative_to(settings.BASE_DIR)))
    for path in sorted(python_files()):
        for s in extract_python(path.read_text(encoding="utf-8")):
            msgids.setdefault(s, str(path.relative_to(settings.BASE_DIR)))
    msgids.pop("", None)
    return msgids


# ---------- .po read/write ----------

def _unquote(s):
    s = s.strip()
    assert s.startswith('"') and s.endswith('"'), s
    return bytes(s[1:-1], "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")


def _quote(s):
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    if "\n" in s:
        lines = s.split("\n")
        out = ['""']
        for k, line in enumerate(lines):
            if k < len(lines) - 1:
                out.append('"' + line + '\\n"')
            elif line:
                out.append('"' + line + '"')
        return "\n".join(out)
    return '"' + s + '"'


def read_po(path):
    entries = {}
    if not Path(path).exists():
        return entries
    msgid = msgstr = None
    cur = None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if msgid is not None and msgstr is not None:
                entries[msgid] = msgstr
                msgid = msgstr = None
            continue
        if line.startswith("msgid "):
            if msgid is not None and msgstr is not None:
                entries[msgid] = msgstr
            msgid, msgstr, cur = _unquote(line[6:]), None, "id"
        elif line.startswith("msgstr "):
            msgstr, cur = _unquote(line[7:]), "str"
        elif line.startswith('"'):
            if cur == "id":
                msgid += _unquote(line)
            else:
                msgstr += _unquote(line)
    if msgid is not None and msgstr is not None:
        entries[msgid] = msgstr
    entries.pop("", None)
    return entries


def write_po(path, entries, sources=None):
    out = ["# GoTani English UI (Phase 7). Source text (msgid) is Indonesian.", 'msgid ""', "msgstr " + _quote(PO_HEADER), ""]
    for key in sorted(entries, key=lambda k: (sources or {}).get(k, "~") + "\0" + k):
        if sources and key in sources:
            out.append(f"#: {sources[key]}")
        out.append("msgid " + _quote(key))
        out.append("msgstr " + _quote(entries[key]))
        out.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(out), encoding="utf-8", newline="\n")


def compile_mo(entries):
    messages = {"": PO_HEADER}
    messages.update({k: v for k, v in entries.items() if v})
    keys = sorted(messages, key=lambda k: k.encode("utf-8"))
    ids = strs = b""
    offsets = []
    for k in keys:
        kb, vb = k.encode("utf-8"), messages[k].encode("utf-8")
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b"\0"
        strs += vb + b"\0"
    keystart = 7 * 4 + 16 * len(keys)
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]
    head = struct.pack("Iiiiiii", 0x950412DE, 0, len(keys), 7 * 4, 7 * 4 + len(keys) * 8, 0, 0)
    return head + array("i", koffsets + voffsets).tobytes() + ids + strs


def po_path(lang="en"):
    return Path(settings.BASE_DIR) / "locale" / lang / "LC_MESSAGES" / "django.po"
