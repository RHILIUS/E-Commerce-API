from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product, ProductImage
from .serializers import ProductCatalogSerializer
from .services.product_recommendations import rank_products_for_user

User = get_user_model()


class Test(APIView):
    permission_classes = [AllowAny]

    def get(self, request):

        users = User.objects.all()
        print(users)

        return Response({"message": "hello"}, status=status.HTTP_200_OK)


class ProductCatalogView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):

        # Fetch products and use an algorithm that show the user prefered products in the catalog first
        products = rank_products_for_user(
            Product.objects.filter(status=Product.Status.ACTIVE), request.user
        ).prefetch_related(
            Prefetch(
                "images",
                queryset=ProductImage.objects.filter(
                    is_default=True).order_by("order", "pk"),
                to_attr="catalog_images",
            )
        )
        serializer = ProductCatalogSerializer(
            products, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
