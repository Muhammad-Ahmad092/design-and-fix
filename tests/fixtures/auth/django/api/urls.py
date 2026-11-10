from django.urls import path

from api import views

urlpatterns = [
    path("health/", views.health),
    path("orders/", views.OrderCreate.as_view()),
    path("orders/recent/", views.RecentOrders.as_view()),
]
