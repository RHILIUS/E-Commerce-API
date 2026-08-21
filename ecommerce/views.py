from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView


class Test(APIView):
    def get(self, request):

        user = request.user
        print(user)

        return Response({"message": "hello"}, status=status.HTTP_200_OK)
