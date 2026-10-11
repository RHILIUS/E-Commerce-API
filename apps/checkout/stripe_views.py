"""Authenticated online purchase endpoints and a signature-authenticated provider webhook."""
import logging

import stripe
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import PlaceStripeSerializer, StripeSessionSerializer, WebhookResponseSerializer
from .services.events import process_event, receive_event
from .services.online import payment_status, refresh_payment, start_payment
from .services.stripe_provider import verify_webhook

logger = logging.getLogger(__name__)
PAYMENT_RESPONSES = {200: StripeSessionSerializer, 400: OpenApiTypes.OBJECT, 401: OpenApiTypes.OBJECT,
                     404: OpenApiTypes.OBJECT, 409: OpenApiTypes.OBJECT, 503: OpenApiTypes.OBJECT}


class StripeCheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Checkout'], request=PlaceStripeSerializer, responses=PAYMENT_RESPONSES,
                   description='Reserve all selected stores and create or recover one USD Stripe test Checkout Session. Retry identical requests with the same key on uncertain failures. Confirmed unsupported-parameter creation rejections return 400 after cancelling orders and releasing holds; obtain a fresh review.')
    def post(self, request):
        serializer = PlaceStripeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(StripeSessionSerializer(start_payment(request.user, serializer.validated_data)).data)


class StripePaymentView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Checkout'], responses={200: StripeSessionSerializer, 401: OpenApiTypes.OBJECT, 404: OpenApiTypes.OBJECT},
                   description='Read saved owner-only online payment state. Does not call Stripe.')
    def get(self, request, reference):
        return Response(StripeSessionSerializer(payment_status(request.user, reference)).data)

    @extend_schema(tags=['Checkout'], request=None, responses=PAYMENT_RESPONSES,
                   description='Reconcile this owned payment with current Stripe state; safe to retry. No new purchase is created.')
    def post(self, request, reference):
        return Response(StripeSessionSerializer(refresh_payment(request.user, reference)).data)


class StripeCancelView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['Checkout'], request=None, responses=PAYMENT_RESPONSES,
                   description='Persist cancellation intent and expire the session. Release stock only on confirmed terminal unpaid state; an already successful payment completes instead.')
    def post(self, request, reference):
        return Response(StripeSessionSerializer(refresh_payment(request.user, reference, cancel=True)).data)


class StripeWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(tags=['Checkout'], request=OpenApiTypes.OBJECT,
                   responses={200: WebhookResponseSerializer, 400: OpenApiTypes.OBJECT, 503: OpenApiTypes.OBJECT},
                   description='Stripe signature-authenticated test event inbox. Requires Stripe-Signature over the exact raw body; processing failures remain queued for recovery.')
    def post(self, request):
        try:
            event = verify_webhook(request.body, request.headers.get('Stripe-Signature', ''))
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise ValidationError('Invalid Stripe webhook signature or body.') from exc
        saved = receive_event(event)
        if saved:
            try:
                process_event(saved.pk)
            except Exception:
                # Durable receipt is acknowledged; the scheduled reconciler retries local/provider failures.
                logger.warning('Stripe event %s queued for recovery', saved.pk)
        return Response({'received': True})
