from django.urls import path

from . import views


urlpatterns = [
    path('leads/', views.leads, name='leads'),
]
