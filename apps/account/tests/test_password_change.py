"""Challenge ownership, durable limits, atomic changes and cross-device revocation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.db import close_old_connections, IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.account.services.password import change_password
from apps.authentication.models import PasswordChangeState, OTPVerificationRecord
from apps.authentication.services.email_service import OTPDeliveryError
from apps.authentication.serializers import CustomTokenObtainPairSerializer

ROOT = "/api/account/change-password/"
OLD = "OriginalPassword1"
NEW = "UpdatedPassword2"


def user(name="buyer"):
    return get_user_model().objects.create_user(username=name, email=f"{name}@example.com", password=OLD,
        phone_number=f"+63917123456{1 if name == 'buyer' else 2}")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PasswordChangeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user()
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.payload = {"current_password": OLD, "new_password": NEW}

    def issue(self):
        response = self.client.post(ROOT+"request/", self.payload, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        code = mail.outbox[-1].body.split("code is: ")[1].splitlines()[0]
        return response.data["challenge_id"], code

    def ready_to_resend(self):
        PasswordChangeState.objects.filter(user=self.user).update(last_sent_at=timezone.now()-timedelta(seconds=61))

    def confirm(self, challenge, code, **overrides):
        return self.client.post(ROOT+"confirm/", {**self.payload, "challenge_id":str(challenge), "otp":code, **overrides}, format="json")

    def test_ip_throttle_cannot_be_bypassed_with_forwarded_headers(self):
        for index in range(20):
            response = self.client.post(ROOT+'request/', {}, format='json', HTTP_X_FORWARDED_FOR=f'198.51.100.{index}')
            self.assertEqual(response.status_code, 400)
        response = self.client.post(ROOT+'request/', {}, format='json', HTTP_X_FORWARDED_FOR='203.0.113.1')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data['code'], 'rate_limited')
        self.assertIsInstance(response.data['retry_after'], int)

    def test_password_reset_invalidates_issued_challenge(self):
        challenge, code = self.issue()
        self.user.set_password('AnotherPassword3')
        self.user.save(update_fields=['password'])
        self.assertEqual(self.confirm(challenge, code).data['code'], 'challenge_unavailable')

    def test_six_digit_ascii_code_shape_is_required(self):
        challenge, code = self.issue()
        for invalid in ('12345', '1234567', '123456\n', '１２３４５６', 'abcdef'):
            self.assertEqual(self.confirm(challenge, invalid).status_code, 400)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).failed_attempts, 0)

    def test_all_endpoints_require_authentication(self):
        self.client.force_authenticate(None)
        for suffix in ("request/", "resend/", "confirm/"):
            self.assertEqual(self.client.post(ROOT+suffix, {}, format="json").status_code, 401)

    def test_issue_hashes_code_without_password_change_or_registration_record(self):
        challenge, code = self.issue()
        state = PasswordChangeState.objects.get(user=self.user)
        self.assertNotEqual(state.otp_hash, code)
        self.assertEqual(str(state.challenge_id), str(challenge))
        self.assertEqual(state.email, self.user.email)
        self.assertLessEqual((state.expires_at-timezone.now()).total_seconds(), 300)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD))
        self.assertEqual(OTPVerificationRecord.objects.count(), 0)

    def test_success_consumes_code_clears_cookie_notifies_and_revokes_other_device(self):
        refresh = CustomTokenObtainPairSerializer.get_token(self.user)
        old_access = str(refresh.access_token)
        challenge, code = self.issue()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.confirm(challenge, code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies["refresh_token"]["max-age"], 0)
        self.assertEqual(len(mail.outbox), 2)
        self.assertNotIn(NEW, mail.outbox[-1].body)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))
        self.assertEqual(self.user.authentication_version, 1)
        self.assertTrue(PasswordChangeState.objects.get(user=self.user).consumed)
        self.assertEqual(self.confirm(challenge, code).status_code, 400)
        device = APIClient()
        device.credentials(HTTP_AUTHORIZATION=f"Bearer {old_access}")
        self.assertEqual(device.get('/api/account/profile/').status_code, 401)
        device.cookies['refresh_token'] = str(refresh)
        response = device.post('/api/auth/refresh/', {}, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.cookies['refresh_token']['max-age'], 0)
        self.assertEqual(device.post('/api/auth/verify/', {'token':old_access}).status_code, 401)
        device.credentials()
        self.assertEqual(device.post('/api/auth/login/', {'email':self.user.email,'password':OLD}).status_code, 401)
        login = device.post('/api/auth/login/', {'email':self.user.email,'password':NEW})
        self.assertEqual(login.status_code, 200)
        device.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        self.assertEqual(device.get('/api/account/profile/').status_code, 200)
        self.assertEqual(device.post('/api/auth/refresh/', {}).status_code, 200)

    def test_invalid_password_fields_do_not_send_email(self):
        for payload, field in (({**self.payload,'current_password':'wrong'},'current_password'),
                               ({**self.payload,'new_password':OLD},'new_password'),
                               ({**self.payload,'new_password':'weakpassword'},'new_password')):
            response = self.client.post(ROOT+'request/',payload,format='json')
            self.assertEqual(response.status_code,400)
            self.assertIn(field,response.data)
        self.assertEqual(len(mail.outbox),0)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).request_count,3)

    def test_ownership_and_registration_codes_cannot_authorize_change(self):
        challenge, code = self.issue()
        other = user('other')
        self.client.force_authenticate(other)
        response = self.confirm(challenge, code)
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.data['code'],'challenge_unavailable')
        self.client.force_authenticate(self.user)
        self.assertEqual(self.confirm(uuid.uuid4(),code).status_code,400)

    def test_resend_replaces_code_and_preserves_attempt_budget(self):
        challenge, code = self.issue()
        wrong = '000000' if code != '000000' else '000001'
        self.assertEqual(self.confirm(challenge,wrong).status_code,400)
        self.ready_to_resend()
        with patch('apps.account.services.password.secrets.randbelow',return_value=123456):
            response = self.client.post(ROOT+'resend/',{'challenge_id':str(challenge)},format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).failed_attempts,1)
        if code != '123456':
            self.assertEqual(self.confirm(challenge,code).status_code,400)
        self.assertEqual(self.confirm(challenge,'123456').status_code,200)

    def test_failed_attempt_limit_survives_resend_and_requires_restart(self):
        challenge, code = self.issue()
        wrong = '000000' if code != '000000' else '000001'
        for _ in range(5):
            response = self.confirm(challenge,wrong)
            self.assertEqual(response.status_code,400)
        self.assertEqual(response.data['code'],'challenge_exhausted')
        state = PasswordChangeState.objects.get(user=self.user)
        self.assertEqual(state.failed_attempts,5)
        self.assertTrue(state.consumed)
        self.ready_to_resend()
        self.assertEqual(self.client.post(ROOT+'resend/',{'challenge_id':str(challenge)},format='json').status_code,400)
        self.assertEqual(self.confirm(challenge,code).status_code,400)

    def test_expired_code_can_be_resend_but_not_confirmed(self):
        challenge, code = self.issue()
        PasswordChangeState.objects.filter(user=self.user).update(expires_at=timezone.now()-timedelta(seconds=1))
        response = self.confirm(challenge,code)
        self.assertEqual(response.data['code'],'code_expired')
        self.ready_to_resend()
        self.assertEqual(self.client.post(ROOT+'resend/',{'challenge_id':str(challenge)},format='json').status_code,200)

    def test_account_change_invalidates_challenge(self):
        challenge, code = self.issue()
        self.user.email='changed@example.com'
        self.user.save(update_fields=['email'])
        self.assertEqual(self.confirm(challenge,code).data['code'],'challenge_unavailable')
        self.assertTrue(PasswordChangeState.objects.get(user=self.user).consumed)

    def test_current_and_new_password_are_rechecked_at_confirmation(self):
        challenge, code = self.issue()
        response = self.confirm(challenge,code,current_password='wrong')
        self.assertIn('current_password',response.data)
        response = self.confirm(challenge,code,new_password='weakpassword')
        self.assertIn('new_password',response.data)
        self.assertFalse(PasswordChangeState.objects.get(user=self.user).consumed)

    def test_restarts_retain_send_limits_and_invalidate_old_identifiers(self):
        first,code=self.issue()
        for _ in range(4):
            self.ready_to_resend()
            self.issue()
        self.ready_to_resend()
        response=self.client.post(ROOT+'request/',self.payload,format='json')
        self.assertEqual(response.status_code,429)
        self.assertIsInstance(response.data['retry_after'],int)
        self.assertEqual(self.confirm(first,code).status_code,400)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).send_count,5)

    def test_cooldown_and_delivery_failure_preserve_limits(self):
        with patch('apps.account.services.password.send_password_change_code',side_effect=OTPDeliveryError):
            response=self.client.post(ROOT+'request/',self.payload,format='json')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.data['retry_after'],60)
        self.assertEqual(PasswordChangeState.objects.get(user=self.user).send_count,1)
        self.assertEqual(self.client.post(ROOT+'request/',self.payload,format='json').status_code,429)

    def test_resend_delivery_failure_still_invalidates_old_code(self):
        challenge,code=self.issue()
        self.ready_to_resend()
        with patch('apps.account.services.password.send_password_change_code',side_effect=OTPDeliveryError), patch('apps.account.services.password.secrets.randbelow',return_value=654321):
            response=self.client.post(ROOT+'resend/',{'challenge_id':str(challenge)},format='json')
        self.assertEqual(response.status_code,503)
        if code != '654321':
            self.assertEqual(self.confirm(challenge,code).status_code,400)

    def test_notification_failure_does_not_undo_success(self):
        challenge,code=self.issue()
        with self.assertLogs('apps.account.services.password', level='WARNING') as logs:
            with patch('apps.account.services.password.send_password_changed_email',side_effect=OTPDeliveryError), self.captureOnCommitCallbacks(execute=True):
                response=self.confirm(challenge,code)
        self.assertEqual(logs.output, [
            'WARNING:apps.account.services.password:Password-change notification delivery failed',
        ])
        self.assertEqual(response.status_code,200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))

    def test_write_failure_rolls_back_password_version_and_challenge(self):
        challenge,code=self.issue()
        with patch.object(get_user_model(),'save',side_effect=IntegrityError('simulated')):
            response = self.confirm(challenge,code)
            self.assertEqual(response.status_code,503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD))
        self.assertEqual(self.user.authentication_version,0)
        self.assertFalse(PasswordChangeState.objects.get(user=self.user).consumed)

    def test_legacy_token_only_works_before_first_password_change(self):
        legacy=RefreshToken.for_user(self.user)
        self.assertNotIn('auth_version',legacy)
        device=APIClient()
        device.credentials(HTTP_AUTHORIZATION=f'Bearer {legacy.access_token}')
        self.assertEqual(device.get('/api/account/profile/').status_code,200)
        device.cookies['refresh_token']=str(legacy)
        response=device.post('/api/auth/refresh/',{})
        self.assertEqual(response.status_code,200)
        self.assertEqual(RefreshToken(device.cookies['refresh_token'].value)['auth_version'],0)
        challenge,code=self.issue()
        self.assertEqual(self.confirm(challenge,code).status_code,200)
        self.assertEqual(device.get('/api/account/profile/').status_code,401)
        self.assertEqual(device.post('/api/auth/refresh/',{}).status_code,401)

    @override_settings(PASSWORD_CHANGE_MAX_REQUESTS=2)
    def test_invalid_password_attempts_have_durable_account_limit(self):
        for _ in range(2):
            self.client.post(ROOT+'request/',{**self.payload,'current_password':'wrong'},format='json')
        response=self.client.post(ROOT+'request/',self.payload,format='json')
        self.assertEqual(response.status_code,429)

    @override_settings(PASSWORD_CHANGE_MAX_VERIFICATIONS=2)
    def test_verification_budget_survives_replacing_challenge(self):
        challenge,code=self.issue()
        for _ in range(2):
            self.confirm(uuid.uuid4(),code)
        self.ready_to_resend()
        challenge,code=self.issue()
        self.assertEqual(self.confirm(challenge,code).status_code,429)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordConcurrencyTests(TransactionTestCase):
    def test_simultaneous_confirmations_only_change_password_once(self):
        cache.clear()
        owner=user()
        payload={'current_password':OLD,'new_password':NEW}
        issued=change_password(owner.pk,'request',payload)
        code=mail.outbox[-1].body.split('code is: ')[1].splitlines()[0]
        def confirm():
            close_old_connections()
            client=APIClient()
            client.force_authenticate(owner)
            try:
                return client.post(ROOT+'confirm/',{**payload,'challenge_id':str(issued['challenge_id']),'otp':code},format='json').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(lambda _: confirm(),range(2)))
        self.assertEqual(sorted(results),[200,400])
        owner.refresh_from_db()
        self.assertEqual(owner.authentication_version,1)
