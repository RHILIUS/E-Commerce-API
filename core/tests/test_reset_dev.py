"""Exercise destructive reset boundaries without touching a database or Azure."""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.management.commands.reset_dev import Command

MODULE = "core.management.commands.reset_dev"


class ResetDevTests(SimpleTestCase):
    def test_disabled_by_default(self):
        with override_settings(ALLOW_DEV_RESET=False), patch(f"{MODULE}.connection") as db:
            with self.assertRaisesMessage(CommandError, "Reset disabled"):
                call_command("reset_dev", no_input=True)
            db.cursor.assert_not_called()

    @override_settings(ALLOW_DEV_RESET=True)
    def test_pipeline_and_failure_boundaries(self):
        for mode in ("success", "dry", "cancel", "storage_failure", "import_failure", "migration_failure", "seed_failure", "schema_failure", "preflight_failure", "busy"):
            with self.subTest(mode=mode), patch(f"{MODULE}.connection") as db, \
                    patch.object(Command, "_check_database"), \
                    patch(f"{MODULE}.load_snapshot") as snapshot, \
                    patch(f"{MODULE}.validate_seed_data"), \
                    patch.object(Command, "_media_target") as target, \
                    patch(f"{MODULE}.transaction.atomic"), \
                    patch(f"{MODULE}.call_command") as run, \
                    patch("builtins.input", return_value="no"):
                db.vendor = "postgresql"
                db.in_atomic_block = False
                db.settings_dict = dict(HOST="localhost", PORT="5432", NAME="disposable")
                db.cursor.return_value.__enter__.return_value.fetchone.return_value = (mode != "busy",)
                clear = MagicMock()
                target.return_value = (clear, "test media")
                if mode == "storage_failure":
                    clear.side_effect = OSError("storage unavailable")
                if mode == "import_failure":
                    run.side_effect = [None, CommandError("bad taxonomy")]
                if mode == "migration_failure":
                    run.side_effect = CommandError("migration failed")
                if mode == "seed_failure":
                    run.side_effect = [None, None, CommandError("seed failed")]
                if mode == "schema_failure":
                    cursor = db.cursor.return_value.__enter__.return_value
                    def execute(sql):
                        if sql.startswith("DROP SCHEMA"):
                            raise RuntimeError("DDL failed")
                    cursor.execute.side_effect = execute
                if mode == "preflight_failure":
                    snapshot.side_effect = CommandError("invalid snapshot")
                kwargs = dict(stdout=StringIO(), no_input=mode != "cancel", dry_run=mode == "dry")
                if mode in ("storage_failure", "import_failure", "migration_failure", "seed_failure", "schema_failure", "preflight_failure", "busy"):
                    with self.assertRaises(CommandError):
                        call_command("reset_dev", **kwargs)
                else:
                    call_command("reset_dev", **kwargs)
                sql = [c.args[0] for c in db.cursor.return_value.__enter__.return_value.execute.call_args_list]
                destructive = mode in ("success", "storage_failure", "import_failure", "migration_failure", "seed_failure", "schema_failure")
                self.assertEqual('DROP SCHEMA "public" CASCADE' in sql, destructive)
                self.assertEqual(clear.call_count, int(destructive and mode != "schema_failure"))
                expected = {"success": ["migrate", "import_taxonomy", "seed"],
                            "import_failure": ["migrate", "import_taxonomy"],
                            "migration_failure": ["migrate"],
                            "seed_failure": ["migrate", "import_taxonomy", "seed"]}.get(mode, [])
                self.assertEqual([c.args[0] for c in run.call_args_list], expected)
                self.assertEqual(db.close.call_count, int(mode != "busy"))

    def test_local_cleanup_preserves_sources_and_symlink_targets(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            media = base / "media"
            (media / "nested").mkdir(parents=True)
            (media / "nested/image").write_text("upload")
            source = base / "source"
            source.write_text("keep")
            (media / "linked").symlink_to(source)
            with override_settings(BASE_DIR=base, MEDIA_ROOT=media, STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            }):
                clear, _ = Command()._media_target()
                clear()
                self.assertEqual(list(media.iterdir()), [])
                self.assertEqual(source.read_text(), "keep")
            with override_settings(BASE_DIR=base, MEDIA_ROOT=base):
                with self.assertRaises(CommandError):
                    Command()._media_target()

    def test_azure_cleanup_includes_snapshots_and_preserves_container(self):
        with override_settings(STORAGES={"default": {
            "BACKEND": "storages.backends.azure_storage.AzureStorage",
        }}), patch(f"{MODULE}.default_storage") as storage:
            blob = MagicMock()
            blob.name = "uploads/image.jpg"
            storage.client.list_blobs.return_value = [blob]
            clear, _ = Command()._media_target()
            storage.client.delete_blob.assert_not_called()
            clear()
            storage.client.delete_blob.assert_called_once_with(blob.name, delete_snapshots="include")
            storage.client.delete_container.assert_not_called()

    def test_rejects_additional_schemas_and_custom_search_path(self):
        for schemas, search_path, valid in (
            ([('public',)], '"$user", public', True),
            ([('public',)], 'public', True),
            ([('public',), ('other_app',)], 'public', False),
            ([('public',)], 'other_app, public', False),
            ([], 'public', False),
        ):
            with self.subTest(schemas=schemas, search_path=search_path), patch(f"{MODULE}.connection") as db:
                cursor = db.cursor.return_value.__enter__.return_value
                cursor.fetchall.return_value = schemas
                cursor.fetchone.return_value = (search_path,)
                if valid:
                    Command()._check_database()
                else:
                    with self.assertRaises(CommandError):
                        Command()._check_database()

    @override_settings(ALLOW_DEV_RESET=True)
    def test_rejects_unsupported_database_and_existing_transaction(self):
        for vendor, in_transaction in (("sqlite", False), ("postgresql", True)):
            with self.subTest(vendor=vendor, in_transaction=in_transaction), patch(f"{MODULE}.connection") as db:
                db.vendor = vendor
                db.in_atomic_block = in_transaction
                with self.assertRaises(CommandError):
                    call_command("reset_dev", no_input=True)
                db.cursor.assert_not_called()
