"""Verify lot and input mass-balance invariants (rule 15, D20). Exits non-zero on failure."""

from django.core.management.base import BaseCommand, CommandError

from inventory.services import check_mass_balance, mass_balance_summary


class Command(BaseCommand):
    help = "Check that every lot and input item balances against its append-only ledger."

    def handle(self, *args, **options):
        errors = check_mass_balance()
        if errors:
            for error in errors:
                self.stderr.write(self.style.ERROR(f"✗ {error}"))
            raise CommandError(f"Mass balance FAILED: {len(errors)} pelanggaran.")
        s = mass_balance_summary()
        self.stdout.write(
            self.style.SUCCESS(
                f"Mass balance OK: {s['lots']} lot, {s['ledger_rows']} baris ledger stok, "
                f"{s['open_kg']} kg tersedia, {s['input_items']} input."
            )
        )
