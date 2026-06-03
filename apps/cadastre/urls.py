from django.urls import path

from . import views


urlpatterns = [
    path('', views.coverage_map, name='cadastre-coverage'),
    path('coverage.geojson', views.coverage_geojson, name='cadastre-coverage-geojson'),
    path('rcodes/', views.rcode_map, name='cadastre-rcodes'),
    path('rcodes.geojson', views.rcode_geojson, name='cadastre-rcodes-geojson'),
    path('lots/', views.lot_map, name='cadastre-lots'),
    path('lots.geojson', views.lots_geojson, name='cadastre-lots-geojson'),
]
