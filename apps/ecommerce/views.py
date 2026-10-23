from django.contrib.auth import get_user_model
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ProductImage
from .pagination import ProductCatalogPagination
from .serializers import (
    PaginatedProductsSerializer,
    ProductCatalogSerializer,
    ProductDetailSerializer,
)
from .services.display_prices import get_display_rates
from .services.product_recommendations import rank_products_for_user
from .services.order_quantities import with_ordered_totals

User = get_user_model()


class Test(APIView):
    permission_classes = [AllowAny]

    @extend_schema(tags=["Development"], responses={200: dict})
    def get(self, request):

        users = User.objects.all()
        print(users)

        return Response({"message": "hello"}, status=status.HTTP_200_OK)


class ProductCatalogView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Catalog"],
        parameters=[
            OpenApiParameter(
                "q", str, description="Case-insensitive name or short-description search."),
            OpenApiParameter(
                "page", int, description="Page number, starting at 1; twelve products per page."),
        ],
        description="List active products. Signed-in users see categories ranked by their lifetime ordered units first; recent ordered product units rank ties and are the first signal for guests. Both scores count non-cancelled orders regardless of payment or refund status. Recent popularity uses the configured rolling window by order placement, default seven days. price and currency describe the original store price. display_price converts to the account country's currency, or USD for guests. Unavailable conversions retain the original store currency. Conversion is for display only. ordered_count is lifetime units in non-cancelled orders, including unpaid and refunded purchases.",
        responses={200: PaginatedProductsSerializer, 401: dict, 404: dict},
    )
    def get(self, request):
        # Fetch products and use an algorithm that show the user prefered products in the catalog first
        products = Product.objects.filter(
            status=Product.Status.ACTIVE).select_related("store")
        query = request.query_params.get("q", "").strip()
        if query:
            products = products.filter(
                Q(name__icontains=query) | Q(
                    short_description__icontains=query)
            )
        products = rank_products_for_user(
            with_ordered_totals(products), request.user
        ).prefetch_related(
            Prefetch(
                "images",
                queryset=ProductImage.objects.filter(
                    is_default=True).order_by("order", "pk"),
                to_attr="catalog_images",
            )
        )
        paginator = ProductCatalogPagination()
        page = paginator.paginate_queryset(products, request, view=self)
        serializer = ProductCatalogSerializer(
            page, many=True, context={
                "request": request,
                "display_rates": get_display_rates(request.user, {product.store.currency for product in page}),
            })
        return paginator.get_paginated_response(serializer.data)


class ProductDetailView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Catalog"],
        operation_id="shop_product_detail_retrieve",
        description=(
            "Retrieve an active or archived product publicly. Drafts are visible only "
            "to members of that product's store (admin or staff); all other callers "
            "receive 404. Invalid Bearer tokens return 401, including on public products. "
            "can_purchase is true only for active products with stock. It is product "
            "eligibility, not checkout authorization or a stock reservation. Prices "
            "use the same display conversion as the catalog. Store listing count "
            "includes active products only; created_at is store creation, not first sale. "
            "ordered_count uses the same non-cancelled lifetime units as the catalog and store pages."
        ),
        responses={200: ProductDetailSerializer, 401: dict, 404: dict},
    )
    def get(self, request, pk):
        # Show active and archived products to anyone
        visible = Q(status__in=[Product.Status.ACTIVE,
                    Product.Status.ARCHIVED])
        if request.user.is_authenticated:
            # Show draft product when user is a member of the store of the product
            visible |= Q(status=Product.Status.DRAFT,
                         store__members=request.user)
        products = Product.objects.filter(visible).distinct().select_related("store").prefetch_related(
            Prefetch(
                "images", queryset=ProductImage.objects.order_by("order", "pk"))
        )
        product = get_object_or_404(with_ordered_totals(products), pk=pk)
        serializer = ProductDetailSerializer(product, context={
            "request": request,
            "display_rates": get_display_rates(request.user, {product.store.currency}),
            "listed_product_count": product.store.products.filter(status=Product.Status.ACTIVE).count(),
        })
        return Response(serializer.data)
