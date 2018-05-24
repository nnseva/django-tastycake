from django.conf.urls import include, url
from django.conf import settings
from tastycake.api import Api

urlpatterns = [
    url(r'', include(Api().urls)),
]
