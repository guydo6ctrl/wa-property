from django.urls import path

from . import views


urlpatterns = [
    path('listing/<int:listing_id>/map/', views.listing_map, name='listing-map'),
]
