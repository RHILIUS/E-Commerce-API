"""Legacy authorization is discarded without reopening recent account budgets."""
from datetime import timedelta
import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class PasswordChangeMigrationTests(TransactionTestCase):
    def test_legacy_challenges_are_invalidated_and_active_limits_carried(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [('authentication', '0005_password_reset_state')]
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            User = apps.get_model('authentication', 'User')
            State = apps.get_model('authentication', 'PasswordChangeState')
            owner = User.objects.create(username='migration', email='migration@example.com', phone_number='+639171234569')
            now = timezone.now()
            latest_start = now-timedelta(seconds=10)
            State.objects.create(user_id=owner.pk, challenge_id=uuid.uuid4(), consumed=False, otp_hash='legacy',
                expires_at=now+timedelta(minutes=5), last_sent_at=now,
                request_window_started_at=now-timedelta(seconds=950), request_count=20,
                send_window_started_at=latest_start, send_count=5,
                verification_window_started_at=now-timedelta(seconds=100), verification_count=12, failed_attempts=4)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            state = apps.get_model('authentication', 'PasswordChangeState').objects.get(user_id=owner.pk)
            self.assertIsNone(state.challenge_id)
            self.assertTrue(state.consumed)
            self.assertEqual(state.otp_hash, '')
            self.assertIsNone(state.expires_at)
            self.assertEqual(state.grant_hash, '')
            self.assertIsNone(state.grant_expires_at)
            self.assertEqual(state.window_started_at, latest_start)
            self.assertEqual((state.request_count, state.send_count, state.verification_count, state.failed_attempts), (0, 5, 12, 4))
            self.assertEqual(state.last_sent_at, now)
        finally:
            MigrationExecutor(connection).migrate(latest)
