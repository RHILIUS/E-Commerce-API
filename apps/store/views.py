from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ecommerce.models import Category, Product
from apps.ecommerce.services.display_prices import get_display_rates
from apps.ecommerce.services.order_quantities import counted_order_items
from .services.catalog import SORT_FIELDS, store_products
from .models import Store
from .serializers import PublicStoreSerializer, StoreHomeSerializer, StoreProductQuerySerializer, StoreProductsSerializer, StoreProductSerializer


class StoreDetailView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Stores"], responses={200: PublicStoreSerializer, 401: dict, 404: dict},
        description="Public store profile, public contact fields only, active listing count, non-cancelled lifetime units (including historical products), and active taxonomy categories directly assigned to listed products. No member/account data is exposed.",
    )
    def get(self, request, pk):
        store = get_object_or_404(Store.objects.select_related("address"), pk=pk)
        store.listed_product_count = store.products.filter(status=Product.Status.ACTIVE).count()
        store.items_ordered = counted_order_items(timezone.now()).filter(order__store=store).aggregate(total=Sum("quantity"))["total"] or 0
        store.categories = Category.objects.filter(
            is_active=True, products__store=store, products__status=Product.Status.ACTIVE,
        ).annotate(product_count=Count("products")).order_by("name", "pk")
        return Response(PublicStoreSerializer(store, context={"request": request}).data)


class StoreHomeView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Stores"], responses={200: StoreHomeSerializer, 401: dict, 404: dict},
        description="Up to ten active products per section. New: newest first. Trending: positive ordered units in the rolling 30 days by order placement. Most ordered: positive lifetime units. Count all non-cancelled orders, including unpaid and refunded purchases. Ties use newest creation then highest ID. No personalization. Catalog display-price conversion applies.",
    )
    def get(self, request, pk):
        store = get_object_or_404(Store, pk=pk)
        products = store_products(store)
        sections = {
            "new_products": products.order_by(*SORT_FIELDS["latest"])[:10],
            "trending_products": products.filter(ordered_recent__gt=0).order_by(*SORT_FIELDS["popular"])[:10],
            "most_ordered": products.filter(ordered_total__gt=0).order_by(*SORT_FIELDS["most_ordered"])[:10],
        }
        return Response(StoreHomeSerializer(sections, context={
            "request": request, "display_rates": get_display_rates(request.user, {store.currency}),
        }).data)


class StoreProductsPagination(PageNumberPagination):
    page_size = 10


class StoreProductsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Stores"], parameters=[StoreProductQuerySerializer, OpenApiParameter("page", int, description="1-based page; ten products per page.")],
        responses={200: StoreProductsSerializer, 400: dict, 401: dict, 404: dict},
        description="Active products from this store only. q is a trimmed case-insensitive partial name/short-description match. category matches direct taxonomy assignment (no descendant expansion). Invalid/inactive category returns 404; an active category with no matching products returns an empty page. Sort defaults to latest; popular uses rolling 30-day ordered units, most_ordered lifetime units, then newest creation and highest ID. Latest/oldest use creation and ID. Zero-order products remain browsable. Same ordered-unit and price rules as store home. Invalid pages return 404.",
    )
    def get(self, request, pk):
        store = get_object_or_404(Store, pk=pk)
        query = StoreProductQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        products = store_products(store)
        if params["q"]:
            products = products.filter(Q(name__icontains=params["q"]) | Q(short_description__icontains=params["q"]))
        if "category" in params:
            category = get_object_or_404(Category, pk=params["category"], is_active=True)
            products = products.filter(category=category)
        products = products.order_by(*SORT_FIELDS[params["sort"]])
        paginator = StoreProductsPagination()
        page = paginator.paginate_queryset(products, request, view=self)
        data = StoreProductSerializer(page, many=True, context={
            "request": request, "display_rates": get_display_rates(request.user, {store.currency}),
        }).data
        return paginator.get_paginated_response(data)
