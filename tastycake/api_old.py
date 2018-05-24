from django.conf.urls import url, include
from django.http import HttpResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.apps import apps

from django.conf import settings

from tastypie.exceptions import (
    TastypieError,
    HydrationError,
    NotRegistered,
    NotFound,
    Unauthorized,
    ApiFieldError,
    UnsupportedFormat,
    BadRequest,
    BlueberryFillingFound,
    InvalidFilterError,
    InvalidSortError,
    ImmediateHttpResponse,
)

from tastypie.serializers import Serializer
from tastypie.utils.mime import determine_format, build_content_type
from tastypie.utils import is_valid_jsonp_callback_value, string_to_python, trailing_slash

import re
import traceback
import sys
import copy
from importlib import import_module

import logging
logger = logging.getLogger(__name__)

class BaseApi(object):
    @staticmethod
    def _get_error(request,ex):
        ret = {
            "error": type(ex).__name__,
            "description": "%s" % ex,
            "request": {
                "method": request.method,
                "path": request.path,
                "GET": request.GET,
                "COOKIES": request.COOKIES,
                "META": {i[0]:i[1] for i in request.META.items() if i[0] != "HTTP_COOKIE" and (i[0].startswith('HTTP_') or i[0].startswith('CONTENT_') or i[0].startswith('REMOTE_'))}
            }
        }
        if getattr(settings,'TASTYCAKE_RETURN_BODY_ON_ERROR',False) and request.body:
            ret['request']['body'] = request.body
        return ret

    @staticmethod
    def _check_method(request,methods):
        if not request.method.lower() in methods:
            raise BadRequest('Forbidden method: %s' % request.method)

    def __init__(self,serializer_class=Serializer):
        self.serializer_class = serializer_class
        self.serializer = serializer_class()

    def wrap_view(self, view_func):
        @csrf_exempt
        def wrapper(request, *args, **kwargs):
            try:
                return self.create_response(view_func(request, *args, **kwargs),request, *args, **kwargs)
            except ImmediateHttpResponse as ex:
                return ex.response
            except (NotRegistered, NotFound, Http404) as ex:
                ret = self.create_response(self._get_error(request, ex), request)
                ret.status_code = 404
                return ret
            except Unauthorized as ex:
                ret = self.create_response(self._get_error(request, ex), request)
                ret.status_code = 403
                return ret
            except TastypieError as ex:
                ret = self.create_response(self._get_error(request, ex), request)
                ret.status_code = 400
                return ret

            #except HydrationError:
            #except ApiFieldError:
            #except UnsupportedFormat:
            #except BadRequest:
            #except BlueberryFillingFound:
            #except InvalidFilterError:
            #except InvalidSortError:
            except Exception as ex:
                ret = self._get_error(request, ex)
                if settings.DEBUG:
                    ret["stack"] = traceback.format_tb(sys.exc_info()[2])
                ret = self.create_response(ret, request)
                ret.status_code = 500
                return ret
        return wrapper

    @property
    def urls(self):
        return []

    def create_response(self, data, request, *args, **kwargs):
        desired_format = determine_format(request, self.serializer)

        options = {}
        if 'text/javascript' in desired_format:
            callback = request.GET.get('callback', 'callback')

            if not is_valid_jsonp_callback_value(callback):
                raise BadRequest('JSONP callback name is invalid.')

            options['callback'] = callback

        serialized = self.serializer.serialize(data, desired_format, options)
        return HttpResponse(content=serialized, content_type=build_content_type(desired_format))

class Api(BaseApi):
    def __init__(self,serializer_class=Serializer):
        super(Api,self).__init__(serializer_class=serializer_class)

        self.settings = {'v1':{}}
        if hasattr(settings,'TASTYCAKE'):
            self.settings = settings.TASTYCAKE

    @property
    def urls(self):
        ret = [
            url(r'^$',self.wrap_view(self.get_versions_view), name="get_versions"),
            url(r'^(?P<version>[^/]*)/?$',self.wrap_view(self.dispatch_version),name="get_version"),
            url(r'^(?P<version>[^/]*)/(?P<app>[^/]*)/?$',self.wrap_view(self.dispatch_app), name="get_application"),
        ]
        for v in self._get_versions():
            for a in self._get_applications(v):
                for m in self._get_application_models(v, a):
                    ret.append(
                        url(r'^(?P<version>%(version)s)/(?P<app>%(application)s)/?',
                            include(self._get_model_resource(v, a, m).urls)
                        )
                    )
        return ret


    def get_versions_view(self,request,**kw):
        return self._get_versions()

    def _get_versions(self):
        versions = [v for v in self.settings]
        return versions

    def dispatch_version(self,request,version=None,**kw):
        self._check_method(request,['get'])
        if not version in self.settings:
            raise NotFound("No such version: %s" % version)
        ret = {
            'version':version,
            'apps': self._get_applications(version)
        }

        for k in settings:
            if k in ('name','date','description','deprecated','readonly'):
                ret[k] = settings[k]

        return ret

    def _get_applications(self, version):
        settings = self.settings[version]
        settings_apps = settings.get('apps',{})
        settings_exclude = settings.get('exclude',{})
        ret = set([])

        for config in apps.get_app_configs():
            if config.label in settings_exclude:
                continue
            ret.add(config.label)

        for s in settings_apps:
            if not s in ret:
                ret.add(s)

        return list(ret)

    def dispatch_app(self,request,version=None,app=None,**kw):
        self._check_method(request,['get'])
        if not version in self.settings:
            raise NotFound("No such version: %s" % version)
        settings = self.settings[version]
        return self._get_application(request, version, app, settings)


    def _get_application(self, version, app):
        settings = self.settings[version]
        settings_exclude = settings.get('exclude',{})

        if app in settings_exclude:
            raise NotRegistered("No such application label: %s" % app)

        settings_app = settings.get('apps',{}).get(app,{})
        ret = {}

        try:
            config = apps.get_app_config(app)
            ret = {
                'label':config.label,
                'verbose_name':config.verbose_name,
            }
        except LookupError:
            pass

        for n in settings_app:
            ret[n] = settings_app[n]
            ret['label'] = app
        if not ret:
            raise NotRegistered("No such application label: %s" % app)

        ret['models'] = self._get_application_models(version, app)

        return ret

    def _get_application_models(self, version, app):
        settings = self.settings[version]
        settings_exclude = settings.get('exclude',{})
        settings_app = settings.get('apps',{}).get(app,{})

        ret = set([])

        try:
            config = apps.get_app_config(app)
            for m in config.get_models():
                if not "%s.%s" % (m._meta.app_label,m._meta.model_name) in settings_exclude:
                    ret.add(m._meta.model_name)
        except LookupError:
            pass
        for m in settings_app.get('models',{}):
            if not "%s.%s" % (app,m) in settings_exclude:
                ret.add(m)
        return list(ret)

    def _get_model_resource(self, version, app, model):
        pass
