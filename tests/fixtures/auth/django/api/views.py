from django.http import JsonResponse
from rest_framework import generics
from rest_framework.views import APIView


def health(request):
    return JsonResponse({"ok": True})


class OrderCreate(generics.CreateAPIView):
    serializer_class = None


class RecentOrders(APIView):
    def get(self, request):
        return JsonResponse([], safe=False)
