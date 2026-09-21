from django.contrib.auth import get_user_model
from django.db.models import Prefetch, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ProductImage
from .pagination import ProductCatalogPagination
from .serializers import PaginatedProductsSerializer, ProductCatalogSerializer
from .services.product_recommendations import rank_products_for_user
from .services.display_prices import get_display_rates

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
            OpenApiParameter("q", str, description="Case-insensitive name or short-description search."),
            OpenApiParameter("page", int, description="Page number, starting at 1; eight products per page."),
        ],
        description="List active products. Signed-in users see preferred categories first; recent product popularity ranks ties and is the first signal for guests. price and currency describe the original store price. display_price converts to the account country's currency, or USD for guests. Unavailable conversions retain the original store currency. Conversion is for display only.",
        responses={200: PaginatedProductsSerializer, 401: dict, 404: dict},
    )
    def get(self, request):
        # Fetch products and use an algorithm that show the user prefered products in the catalog first
        products = Product.objects.filter(status=Product.Status.ACTIVE).select_related("store")
        query = request.query_params.get("q", "").strip()
        if query:
            products = products.filter(
                Q(name__icontains=query) | Q(
                    short_description__icontains=query)
            )
        products = rank_products_for_user(
            products, request.user
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
