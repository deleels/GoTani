"""Extract msgids, merge them into locale/en/LC_MESSAGES/django.po and compile the .mo (Phase 7).

    python manage.py i18n            # sync + compile
    python manage.py i18n --missing  # list msgids without English, then exit
"""

from django.core.management.base import BaseCommand

from core import i18n


class Command(BaseCommand):
    help = "Sync and compile the English translation catalog (no GNU gettext needed)."

    def add_arguments(self, parser):
        parser.add_argument("--missing", action="store_true", help="Only list untranslated msgids.")
        parser.add_argument("--prune", action="store_true", help="Drop entries no longer found in the code.")

    def handle(self, *args, **opts):
        found = i18n.extract_all()
        path = i18n.po_path("en")
        entries = i18n.read_po(path)
        if opts["missing"]:
            missing = [k for k in found if not entries.get(k)]
            for k in missing:
                self.stdout.write(f"{found[k]}\t{k!r}")
            self.stdout.write(f"{len(missing)} untranslated of {len(found)}")
            return
        for k in found:
            entries.setdefault(k, "")
        # Hand-written English lives in locale/en/tr_*.py as TR = {msgid: english}; it wins over the .po.
        for src in sorted(path.parent.parent.glob("tr_*.py")):
            scope = {}
            exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), scope)
            for k, v in scope.get("TR", {}).items():
                if k in entries or k in found:
                    entries[k] = v
        if opts["prune"]:
            entries = {k: v for k, v in entries.items() if k in found}
        i18n.write_po(path, entries, found)
        path.with_suffix(".mo").write_bytes(i18n.compile_mo(entries))
        done = sum(1 for k in found if entries.get(k))
        self.stdout.write(self.style.SUCCESS(f"{done}/{len(found)} translated -> {path.with_suffix('.mo')}"))
