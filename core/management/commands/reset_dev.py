"""Rebuild a disposable development environment without resetting infrastructure."""

import shutil
from pathlib import Path

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.ecommerce.services.taxonomy_import import load_snapshot
from core.management.commands.seed import validate_seed_data


class Command(BaseCommand):
    help = "Destructively rebuild the public schema and clear all configured media, then migrate/import/seed."
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("--directory", type=Path,
                            default=Path(settings.BASE_DIR) / "data/taxonomy/2026-08")
        parser.add_argument("--dry-run", action="store_true",
                            help="Check prerequisites and show targets without deleting or writing data.")
        parser.add_argument("--no-input", action="store_true",
                            help="Skip the interactive confirmation; ALLOW_DEV_RESET is still required.")

    def handle(self, *args, **options):
        if not getattr(settings, "ALLOW_DEV_RESET", False):
            raise CommandError("Reset disabled. Set ALLOW_DEV_RESET=True only for disposable environments.")
        if connection.vendor != "postgresql" or connection.in_atomic_block:
            raise CommandError("Reset requires PostgreSQL outside an existing transaction.")
        stage = "preflight"
        locked = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(186430, 1)")
                locked = cursor.fetchone()[0]
                if not locked:
                    raise CommandError("Another reset_dev command is running against this database.")
            self._check_database()
            directory = options["directory"].resolve()
            load_snapshot(directory / "categories.en.json")
            validate_seed_data()
            clear_media, media_target = self._media_target()
            config = connection.settings_dict
            self.stdout.write(
                f"DELETE database schema: {config['HOST']}:{config['PORT']}/{config['NAME']} schema=public\n"
                f"DELETE all uploaded media: {media_target}\n"
                f"Taxonomy: {directory}\n"
                "Stop application servers and workers before proceeding. This cannot be rolled back as a whole."
            )
            if options["dry_run"]:
                self.stdout.write("Dry run complete. No data deleted or written; write permissions were not tested.")
                return
            if not options["no_input"] and input("Type RESET to permanently delete this data: ") != "RESET":
                self.stdout.write("Reset cancelled. No data deleted.")
                return
            stage = "database schema reset"
            self.stdout.write(f"Starting {stage}...")
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('DROP SCHEMA "public" CASCADE')
                    cursor.execute('CREATE SCHEMA "public" AUTHORIZATION CURRENT_USER')
            stage = "media deletion"
            self.stdout.write(f"Starting {stage}...")
            clear_media()
            for stage, command, arguments in (
                ("migrations", "migrate", {"interactive": False}),
                ("taxonomy import", "import_taxonomy", {"directory": directory}),
                ("seed", "seed", {}),
            ):
                self.stdout.write(f"Starting {stage}...")
                call_command(command, stdout=self.stdout, stderr=self.stderr, **arguments)
            self.stdout.write(self.style.SUCCESS("Development reset complete."))
        except Exception as error:
            raise CommandError(
                f"reset_dev stopped during {stage}: {error}. "
                "If deletion started, the environment may be partially reset. "
                "Correct the problem and rerun the full command."
            ) from error
        finally:
            if locked:
                # Closing the session also releases the advisory lock, even after connection errors.
                connection.close()

    def _check_database(self):
        """Reject unsupported schema layouts before any destructive operation."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT nspname FROM pg_namespace "
                "WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'"
            )
            if {row[0] for row in cursor.fetchall()} != {"public"}:
                raise CommandError("Reset supports only a database with public as its sole user schema.")
            cursor.execute("SHOW search_path")
            if cursor.fetchone()[0] not in ('"$user", public', 'public'):
                raise CommandError("Reset requires the default public search path.")

    def _media_target(self):
        """Preflight the configured backend and return its scoped deletion operation."""
        backend = settings.STORAGES["default"]["BACKEND"]
        if backend == "storages.backends.azure_storage.AzureStorage":
            container = default_storage.client
            container.get_container_properties()
            # Force an authenticated listing before resetting the database.
            next(iter(container.list_blobs()), None)

            def clear_azure():
                """Delete current blobs and their snapshots without deleting the container."""
                for blob in container.list_blobs():
                    container.delete_blob(blob.name, delete_snapshots="include")

            return clear_azure, f"Azure account={default_storage.account_name} container={default_storage.azure_container}"
        if backend != "django.core.files.storage.FileSystemStorage":
            raise CommandError("Unsupported media storage backend.")
        root = Path(settings.MEDIA_ROOT)
        expected = Path(settings.BASE_DIR).resolve() / "media"
        if root.is_symlink() or root.resolve() != expected or (root.exists() and not root.is_dir()):
            raise CommandError("Local reset requires the backend's dedicated media directory without symlinks.")

        def clear_local():
            """Remove uploaded children while preserving the media directory and symlink targets."""
            if root.exists():
                for child in root.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()

        return clear_local, str(root.resolve())
