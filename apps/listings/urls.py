from django.urls import path

from . import views


urlpatterns = [
    path('listing/<int:listing_id>/map/', views.listing_map, name='listing-map'),
    path('cluster/<int:cluster_id>/map/', views.cluster_map, name='cluster-map'),
]
