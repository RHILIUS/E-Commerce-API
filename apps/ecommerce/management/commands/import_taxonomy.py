from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.ecommerce.services.taxonomy_import import import_snapshot, load_snapshot


class Command(BaseCommand):
    help = "Import categories, attributes, and their associations from a local Shopify snapshot."

    def add_arguments(self, parser):
        parser.add_argument("--directory", required=True, type=Path,
                            help="Version directory containing categories.en.json.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Validate and report planned changes without writing to the database.")

    def handle(self, *args, **options):
        snapshot = load_snapshot(options["directory"] / "categories.en.json")
        self.stdout.write(
            f"Validated Shopify {snapshot.version}: {len(snapshot.categories)} categories, "
            f"{len(snapshot.attributes)} attributes, {len(snapshot.associations)} associations."
        )
        try:
            counts = import_snapshot(snapshot, dry_run=options["dry_run"])
        except DatabaseError as error:
            raise CommandError(
                "Taxonomy import could not access or update the database. "
                "Check connectivity, apply pending migrations, and retry."
            ) from error
        for model, values in counts.items():
            self.stdout.write(f"{model}: " + ", ".join(f"{key}={value}" for key, value in values.items()))
        self.stdout.write("AttributeValue choices are not included in this file; no values or product specifications were imported.")
        self.stdout.write(self.style.SUCCESS(
            "Dry run complete; no database writes performed." if options["dry_run"]
            else "Taxonomy import complete. Existing records and associations were preserved."
        ))
