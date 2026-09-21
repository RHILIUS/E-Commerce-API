from rest_framework.pagination import PageNumberPagination


class ProductCatalogPagination(PageNumberPagination):
    page_size = 8
