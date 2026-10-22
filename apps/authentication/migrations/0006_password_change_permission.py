"""Invalidate legacy challenges while conservatively preserving recent account limits."""
from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def carry_limits(apps, schema_editor):
    """Carry active counters into the latest window so migration cannot reopen exhausted budgets."""
    State = apps.get_model("authentication", "PasswordChangeState")
    now = timezone.now()
    for state in State.objects.using(schema_editor.connection.alias).iterator():
        active = []
        for prefix in ("request", "send", "verification"):
            started = getattr(state, f"{prefix}_window_started_at")
            if started + timedelta(seconds=900) > now:
                active.append(started)
            else:
                setattr(state, f"{prefix}_count", 0)
        state.window_started_at = max(active) if active else now
        if not active:
            state.failed_attempts = 0
        # No legacy code may authorize the new two-step permission flow.
        state.challenge_id = None
        state.consumed = True
        state.otp_hash = ""
        state.expires_at = None
        state.save()


class Migration(migrations.Migration):
    dependencies = [("authentication", "0005_password_reset_state")]
    operations = [
        migrations.AddField(model_name="passwordchangestate", name="window_started_at",
                            field=models.DateTimeField(default=timezone.now), preserve_default=False),
        migrations.AddField(model_name="passwordchangestate", name="grant_hash",
                            field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="passwordchangestate", name="grant_expires_at",
                            field=models.DateTimeField(null=True)),
        migrations.RunPython(carry_limits, migrations.RunPython.noop),
        migrations.RemoveField(model_name="passwordchangestate", name="send_window_started_at"),
        migrations.RemoveField(model_name="passwordchangestate", name="request_window_started_at"),
        migrations.RemoveField(model_name="passwordchangestate", name="verification_window_started_at"),
    ]
