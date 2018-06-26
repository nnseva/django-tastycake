from __future__ import unicode_literals

from django.conf.urls import url, include
from django.http import HttpResponse, Http404, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.apps import apps

from django.db.models import Q, F
from django.db.models.fields.related import ForeignKey, ManyToManyField, OneToOneField
from django.db.models.fields.reverse_related import ForeignObjectRel, OneToOneRel, ManyToOneRel, ManyToManyRel

from django.utils.translation import ugettext_lazy as _, get_language

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
from tastypie.api import Api as TastypieApi
from tastypie.http import HttpNoContent
from tastypie.resources import Resource, ModelResource
from tastypie.constants import ALL,ALL_WITH_RELATIONS

from tastypie.authentication import MultiAuthentication,SessionAuthentication
from tastypie.authorization import ReadOnlyAuthorization

from tastypie import fields

import re
import traceback
import sys
import copy
import json

from urllib import urlencode

from importlib import import_module

import logging
logger = logging.getLogger(__name__)

class TastycakeError(TastypieError):
    pass

class BaseApiMixin:
    @staticmethod
    def _get_error(request, ex, return_body=False):
        ret = {
            "error": type(ex).__name__,
            "description": "%s" % ex,
            "request": {
                "method": request.method,
                "path": request.path,
                "GET": {k:request.GET.getlist(k) for k in request.GET},
                "COOKIES": request.COOKIES,
                "META": {i[0]:i[1] for i in request.META.items() if i[0] != "HTTP_COOKIE" and (i[0].startswith('HTTP_') or i[0].startswith('CONTENT_') or i[0].startswith('REMOTE_'))}
            }
        }
        if return_body and request.body:
            ret['request']['body'] = request.body
        return ret

    @staticmethod
    def _import_function(function_ref):
        if callable(function_ref):
            return function_ref
        function_path = function_ref.rsplit('.',1)
        if len(function_path) < 2:
            raise TastycakeError("Bad function reference: %s" % function_ref)
        try:
            module = import_module(function_path[0])
        except ImportError, ex:
            raise TastycakeError("Bad function module: %s" % ex)

        function_callable = getattr(module, function_path[1], None)
        if not function_callable:
            raise TastycakeError("No function implementation: %s" % function_ref)
        return function_callable

    @staticmethod
    def _check_method(request,methods):
        if not request.method.lower() in methods:
            raise BadRequest('Forbidden method: %s' % request.method)

    def wrap_view(self, view_func_name):
        view_func = getattr(self, view_func_name, None)
        if not view_func:
            ret = self.create_response(request, self._get_error(request, TastycakeError("No such view: %s" % view_func_name)))
            ret.status_code = 500
            return ret
        return self.wrap_function(view_func)

    def wrap_function(self, view_func):
        @csrf_exempt
        def wrapper(request, *args, **kwargs):
            try:
                return self.create_response(request, view_func(request, *args, **kwargs), *args, **kwargs)
            except ImmediateHttpResponse as ex:
                return ex.response
            except (NotRegistered, NotFound, Http404) as ex:
                ret = self.create_response(request, self._get_error(request, ex))
                ret.status_code = 404
                return ret
            except Unauthorized as ex:
                ret = self.create_response(request, self._get_error(request, ex))
                ret.status_code = 403
                return ret
            except TastycakeError as ex:
                ret = self.create_response(request, self._get_error(request, ex))
                ret.status_code = 500
                return ret
            except TastypieError as ex:
                ret = self.create_response(request, self._get_error(request, ex))
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
                ret = self.create_response(request, ret)
                ret.status_code = 500
                return ret
        return wrapper
    def create_response(self, request, data, response_class=HttpResponse, *args, **kwargs):
        if isinstance(data, HttpResponse):
            return data
        if hasattr(self, '_meta'):
            serializer = self._meta.serializer
        else:
            serializer = self.serializer
        desired_format = determine_format(request, serializer)

        options = {}
        if 'text/javascript' in desired_format:
            callback = request.GET.get('callback', 'callback')

            if not is_valid_jsonp_callback_value(callback):
                raise BadRequest('JSONP callback name is invalid.')

            options['callback'] = callback

        serialized = "{}"
        if data:
            serialized = serializer.serialize(data, desired_format, options)
        return response_class(content=serialized, content_type=build_content_type(desired_format))

class BaseApi(BaseApiMixin, object):
    def __init__(self, serializer_class=Serializer):
        self.serializer_class=serializer_class
        self.serializer=serializer_class()

    def prepend_urls(self):
        return []

    @property
    def urls(self):
        return self.prepend_urls()

class Api(BaseApi):
    def __init__(self, settings_local=None, settings_name='TASTYCAKE', serializer_class=Serializer):
        super(Api,self).__init__(serializer_class=Serializer)
        self.settings = {'v1':{}}
        if settings_local:
            self.settings = settings_local
        elif hasattr(settings, settings_name):
            self.settings = getattr(settings, settings_name)

        if isinstance(self.settings, basestring):
            module, name = self.settings.rsplit('.',1)
            module = import_module(module)
            self.settings = getattr(module, name)

        self.version_resources = {}

        for v in self.settings:
            self.version_resources[v] = self.create_version_resource(v)

    def create_version_resource(self, version):
        return VersionApi(self, version, self.settings[version])

    def prepend_urls(self):
        ret = [
            url('^$',self.get_versions_view,name="get_versions")
        ]
        for v in self.version_resources:
            ret.append(url('',include(self.version_resources[v].urls)))
        return ret + super(Api,self).prepend_urls()

    def get_versions_view(self, request, *args, **kwargs):
        return self.create_response(request, {
            v: self.version_resources[v].build_schema()
            for v in self.version_resources
        }, *args, **kwargs)

class VersionApi(BaseApiMixin, TastypieApi):
    def __init__(self, api, version, settings, serializer_class=Serializer):
        super(VersionApi,self).__init__(api_name=version, serializer_class=serializer_class)
        self.api = api
        self.serializer_class = serializer_class
        self.settings = settings
        self.application_resources = {}

        self.default_authentication = SessionAuthentication()
        self.default_authorization = ReadOnlyAuthorization()

        applications = set(
            [config.label for config in apps.get_app_configs() if list(config.get_models())]
        ).difference(self.settings.get('exclude',set([])))

        for a in applications:
            self.application_resources[a] = self.create_application_resource(a)
            self.application_resources[a].register_model_resources(self)

    def prepend_urls(self):
        return [
            url(r"^(?P<api_name>%s)/" % (self.api_name), include(self.application_resources[a].urls))
            for a in self.application_resources
        ]

    def create_application_resource(self, application):
        app_settings = copy.deepcopy(self.settings.get('apps',{}).get(application,{}))
        app_settings['exclude'] = [
            e.split('.',1)[1]
            for e in self.settings.get('exclude',[])
            if e.split('.')[0] == application and len(e.split('.')) >= 2
        ] + app_settings.get('exclude',[])
        return ApplicationApi(self, self.api_name, application, app_settings, serializer_class=self.serializer_class)

    def build_schema(self, detailed=False):
        ret = {
            'version':self.api_name,
            'name':self.settings.get('name',None),
            'description':self.settings.get('description',None),
            'verbose_name':self.settings.get('verbose_name',self.settings.get('name',None)),
        }
        if detailed:
            ret['applications'] = {
                a:self.application_resources[a].build_schema()
                for a in self.application_resources
            }
        return ret

    def top_level(self, request, api_name=None, *args, **kwargs):
        return self.create_response(request, self.build_schema(detailed=True), *args, **kwargs)

    def get_authentication(self, model):
        if not 'authentication' in self.settings:
            return self.get_default_authentication()
        return self._import_function(self.settings['authentication'])(model)

    def get_authorization(self, model):
        if not 'authorization' in self.settings:
            return self.get_default_authorization()
        return self._import_function(self.settings['authorization'])(model)

    def get_default_authentication(self):
        return self.default_authentication

    def get_default_authorization(self):
        return self.default_authorization


class ApplicationApi(BaseApi):
    def __init__(self, version_api, version, application, settings, serializer_class=Serializer):
        super(ApplicationApi,self).__init__(serializer_class=serializer_class)
        self.version_api = version_api
        self.version = version
        self.application = application
        self.settings = settings

        models = set(
            self.settings.get('models',{}).keys()
        )

        try:
            app_config = apps.get_app_config(application)
            models = models.union([model._meta.model_name for model in app_config.get_models()])
        except LookupError:
            pass

        models = models.difference(self.settings.get('exclude',set([])))

        self.app_config = app_config
        self.model_resources = {
            m:self.create_model_resource(m) for m in models
        }

    def prepend_urls(self):
        return [
            url('^(?P<application>%s)/?$' % self.application, self.get_schema_view, name='get_application_schema')
        ]

    def build_schema(self, details=False):
        ret = {
            'version':
                self.settings.get('version',
                    getattr(self.app_config.module, '__version__',
                        getattr(self.app_config.module, 'VERSION',
                            None
                        )
                    )
                ),
            'application':self.application,
            'description':
                self.settings.get('description',
                    getattr(self.app_config.module, '__doc__',
                        ""
                    ),
                ),
            'verbose_name':self.settings.get('verbose_name', getattr(self.app_config, 'verbose_name', self.application)),
        }
        if details:
            ret['models'] = {
                m:self.model_resources[m].build_schema()
                for m in self.model_resources
            }
        
        return ret

    def get_schema_view(self, request, application=None, *args, **kwargs):
        return self.create_response(request, self.build_schema(details=True), *args, **kwargs)

    def register_model_resources(self, version_api):
        for m in self.model_resources:
            version_api.register(self.model_resources[m])

    def get_model_settings(self, model_name):
        model_settings = self.settings.get('models',{}).get(model_name,{})
        model_settings['exclude'] = [
            e.split('.',1)[1]
            for e in self.settings.get('exclude',[])
            if e.split('.')[0] == model_name and len(e.split('.')) >= 2
        ] + model_settings.get('exclude',[])
        return model_settings

    def create_model_resource(self,model_name):
        model_class = None
        try:
            app_config = apps.get_app_config(self.application)
            model_class = app_config.get_model(model_name)
        except LookupError:
            pass

        return self.create_model_api(model_class)

    def create_model_api(self, model_class):
        ModelResource = self.get_model_resource_class()
        model_settings = self.get_model_settings(model_class._meta.model_name)
        #class ModelApi(ModelResource):

        class ModelApi(ModelResource):
            class Meta:
                object_class = model_class
                queryset = object_class.objects.all()
                resource_name = "%s/%s" % (self.application, model_class._meta.model_name)
                excludes = model_settings.get('exclude',[])
                always_return_data = False
                include_resource_uri = False
                authentication = self.get_authentication(model_class)
                authorization = self.get_authorization(model_class)
                max_limit = 0

        return ModelApi(self, self.version, self.application, model_class, model_settings)

    def get_model_resource_class(self):
        return CakeModelResource

    def get_authentication(self, model):
        model_settings = self.get_model_settings(model._meta.model_name)
        if 'authentication' in model_settings:
            return self._import_function(model_settings['authentication'])(model)
        if 'authentication' in self.settings:
            return self._import_function(self.settings['authentication'])(model)
        return self.version_api.get_authentication(model)

    def get_authorization(self, model):
        model_settings = self.get_model_settings(model._meta.model_name)
        if 'authorization' in model_settings:
            return self._import_function(model_settings['authorization'])(model)
        if 'authorization' in self.settings:
            return self._import_function(self.settings['authorization'])(model)
        return self.version_api.get_authorization(model)


class ExpressionError(Exception):
    pass

class CakeModelResource(BaseApiMixin, ModelResource):
    def __init__(self, app_api, version, application, model_class, settings):
        super(CakeModelResource,self).__init__()
        self.app_api = app_api
        self.version = version
        self.application = application
        self.settings = settings

    @classmethod
    def get_fields(cls, fields=None, excludes=None):
        final_fields = {}
        fields = fields or []
        excludes = excludes or []

        if not cls._meta.object_class:
            return final_fields

        for f in cls._meta.object_class._meta.fields:
            # If the field name is already present, skip
            if f.name in cls.base_fields:
                continue

            # If field is not present in explicit field listing, skip
            if f.name not in fields:
                continue

            # If field is in exclude list, skip
            if f.name in excludes:
                continue

            if cls.should_skip_field(f):
                continue

            api_field_class = cls.api_field_from_django_field(f)

            kwargs = {
                'attribute': f.name,
                'help_text': f.help_text,
                'verbose_name': f.verbose_name,
            }

            kwargs['use_in'] = 'list' if f.primary_key else 'detail'

            if f.null is True:
                kwargs['null'] = True

            kwargs['unique'] = f.unique

            if not f.null and f.blank is True:
                kwargs['default'] = ''
                kwargs['blank'] = True

            if f.get_internal_type() == 'TextField':
                kwargs['default'] = ''

            if f.has_default():
                kwargs['default'] = f.default

            if getattr(f, 'auto_now', False):
                kwargs['default'] = f.auto_now

            if getattr(f, 'auto_now_add', False):
                kwargs['default'] = f.auto_now_add

            final_fields[f.name] = api_field_class(**kwargs)
            final_fields[f.name].instance_name = f.name

        return final_fields

    @classmethod
    def api_field_from_django_field(cls, f, default=fields.CharField):
        # TODO: extensions
        r = ModelResource.api_field_from_django_field(f, default=default)
        return r

    def alter_list_data_to_serialize(self, request, to_be_serialized):
        # Flattening returned list
        to_be_serialized[self._meta.collection_name] = [v for m in to_be_serialized[self._meta.collection_name] for v in m.data.values()]
        return to_be_serialized

    IGNORE_KEY_PREFIX = '-'
    MODEL_FIELD_PREFIX = '~'

    def get_resource_for_reference(self, field_name):
        if field_name in self.settings.get('exclude',{}):
            return None
        field = self._meta.object_class._meta.get_field(field_name)
        model_to = None
        if isinstance(field, (ForeignKey,ManyToManyField)):
            model_to = field.rel.model
        elif isinstance(field, ForeignObjectRel):
            model_to = field.related_model

        if not model_to:
            return None
        app_resource = self.app_api.version_api.application_resources.get(model_to._meta.app_label, None)
        if not app_resource:
            return None
        return app_resource.model_resources.get(model_to._meta.model_name, None)

    def check_field_access(self, field_name):
        field_ref = field_name.split('__')
        if field_ref[0] in self.settings.get('exclude',{}):
            raise ExpressionError("Field '{}' is excluded".format(field_ref[0]))
        field = self._meta.object_class._meta.get_field(field_ref[0])
        model_to = None
        if isinstance(field, (ForeignKey,ManyToManyField)):
            model_to = field.rel.model
        elif isinstance(field, ForeignObjectRel):
            model_to = field.related_model
        if model_to and len(field_ref) > 1:
            app_resource = self.app_api.version_api.application_resources.get(model_to._meta.app_label, None)
            if not app_resource:
                raise ExpressionError("Application '{}' is excluded".format(model_to._meta.app_label))
            resource = app_resource.model_resources.get(model_to._meta.model_name, None)
            if not resource:
                raise ExpressionError("Model '{}' is excluded".format(model_to._meta.model_name))
            resource.check_field_access('__'.join(field_ref[1:]))
        return field_name

    def get_one_relations(self):
        return [f.name for f in self._meta.object_class._meta.get_fields() if isinstance(f, (ForeignKey, OneToOneRel, OneToOneField))]

    def get_many_relations(self):
        return [f.name for f in self._meta.object_class._meta.get_fields() if isinstance(f, (ManyToManyField, ManyToManyRel, ManyToOneRel))]

    def parse_filter_list(self, items, fn):
        if (not isinstance(items, (list, tuple))):
            if (isinstance(items, (dict))):
                return self.parse_filter_list([{k:items[k]} for k in items], fn)
            raise ExpressionError('Found {}, list or tuple expected'.format(type(items).__name__))
        qset = None
        for item in items:
            qq = self.parse_filter_condition(item)
            if (qset):
                qset = fn(qset, qq)
            else:
                qset = qq
        if (not qset):
            raise ExpressionError('At least one condition must be specified in a list: %s' % (items,))
        return qset

    def parse_filter_condition(self, query):
        if (not isinstance(query, (dict))):
            if isinstance(query, (list, tuple)):
                return self.parse_filter_list(query, lambda x, y: x & y)
            raise ExpressionError('Found {}, dictionary (or list) expected'.format(type(query)))
        if bool(query) & (len(query) > 1):
            return self.parse_filter_list([{k:query[k]} for k in query], lambda x, y: x & y)
        for key, value in query.iteritems():
            if (key == 'or'):
                return self.parse_filter_list(value, lambda x, y: x | y)
            elif (key == 'and'):
                return self.parse_filter_list(value, lambda x, y: x & y)
            elif (key == 'not'):
                return ~self.parse_filter_condition(value)
            elif (key.startswith(self.IGNORE_KEY_PREFIX)):
                return self.parse_filter_condition(value)
            else:
                key = self.check_field_access(key.replace(".","__"))
                if (value is None):
                    key = key + '__isnull'
                    value = True
                elif (isinstance(value, basestring) and value.startswith(self.MODEL_FIELD_PREFIX)):
                    value = F(self.check_field_access(value.replace(self.MODEL_FIELD_PREFIX, '', 1)))
                return Q(**{key: value})
        return Q()

    def build_filters(self, filters=None, ignore_bad_filters=True):
        """
        Example:
          {"or":[{"and":[{"not":{"status":"S"}},{"performer":null}]},{"status":"P"}]}
        Result:
          (OR: (AND: (NOT (AND: (u'status', u'S'))), (u'performer__isnull', True)), (u'status', u'P'))
        """

        if filters is None:
            filters = {}
        orm_filters = {}
        qset = {}
        if ('filter' in filters):
            query = filters['filter']
            try:
                qset = self.parse_filter_condition(json.loads(query))
            except Exception, ex:
                raise InvalidFilterError("%s" % ex)
        return qset

    def apply_filters(self, request, applicable_filters):
        semi_filtered = self.get_object_list(request)
        if applicable_filters:
            try:
                if hasattr(applicable_filters,'keys'):
                    semi_filtered = semi_filtered.filter(**applicable_filters)
                else:
                    semi_filtered = semi_filtered.filter(applicable_filters)
            except Exception,ex:
                raise InvalidFilterError('%s' % ex)
        return semi_filtered.distinct()

    def apply_sorting(self, obj_list, options=None):
        try:
            if not 'order_by' in options:
                return obj_list
            if hasattr(options, 'getlist'):
                order_bits = options.getlist('order_by')
            else:
                order_bits = options.get('order_by')
                if not isinstance(order_bits, (list, tuple)):
                    order_bits = [order_bits]

            order_bits = [b for o in order_bits for b in o.split(',')]

            order_by_args = []
            for order_by in order_bits:
                order = ''
                if order_by.startswith('-'):
                    order = '-'
                    order_by = order_by[1:]
                order_by = self.check_field_access(order_by.replace('.','__'))
                order_by_args.append("%s%s" % (order, order_by))
            return obj_list.order_by(*order_by_args).distinct()
        except Exception, ex:
            raise InvalidSortError('%s' % ex)
        return obj_list.distinct()

    def get_list_endpoint(self):
        return self._build_reverse_url("api_dispatch_list", kwargs={
            'api_name': self._meta.api_name,
            'resource_name': self._meta.resource_name,
        })

    def build_schema(self):
        schema = super(CakeModelResource,self).build_schema()
        list_endpoint = self.get_list_endpoint()
        schema['urls'] = {
            'list_endpoint': list_endpoint,
            'schema': "%s%s/" % (list_endpoint, 'schema'),
            'details': "%s%s/" % (list_endpoint, '<ID>'),
        }
        if self._meta.object_class.__doc__:
            schema['description'] = self._meta.object_class.__doc__

        for field_name in schema['fields']:
            model_field = self._meta.object_class._meta.get_field(field_name)
            settings = self.settings.get('fields',{}).get(field_name,{})
            choices = settings.get('choices',model_field.choices)
            if choices:
                schema['fields'][field_name]['choices'] = dict(choices)
            schema['fields'][field_name]['help_text'] = settings.get('help_text',model_field.help_text)
            schema['fields'][field_name]['verbose_name'] = settings.get('verbose_name',model_field.verbose_name)

        relations = [n for n in self.get_one_relations() + self.get_many_relations() if not n in self.settings.get('exclude',{})]
        if relations:
            schema['relations'] = {}
            for n in relations:
                field = self._meta.object_class._meta.get_field(n)
                resource = self.get_resource_for_reference(n)
                if not resource:
                    continue
                resource_list_endpoint = resource.get_list_endpoint()
                settings = self.settings.get('relations',{}).get(n,{})
                if isinstance(field, (ForeignKey, OneToOneField, ManyToManyField)):
                    schema['relations'][n] = {
                        'name': field.name,
                        'blank': field.blank,
                        'help_text': settings.get('help_text',field.help_text),
                        'nullable': field.null,
                        'primary_key': field.primary_key,
                        'readonly': not field.editable,
                        'unique': field.unique,
                        'verbose_name': settings.get('verbose_name',field.verbose_name),
                        'many': isinstance(field, ManyToManyField),
                        'related': resource_list_endpoint,
                        'original': True,
                    }
                elif isinstance(field, ForeignObjectRel):
                    schema['relations'][n] = {
                        'name': field.name,
                        'help_text': settings.get('help_text',None),
                        'verbose_name': settings.get('verbose_name',
                            field.related_model._meta.verbose_name_plural
                                if isinstance(field, (ManyToManyRel, ManyToOneRel))
                            else field.related_model._meta.verbose_name
                        ),
                        'many': isinstance(field, (ManyToManyRel, ManyToOneRel)),
                        'related': resource_list_endpoint,
                        'original': False,
                    }
                else:
                    # WTF?
                    raise Exception("WTF?")
                schema['relations'][n]['urls'] = {}
                if isinstance(field, (ForeignKey, OneToOneRel, OneToOneField)):
                    schema['relations'][n]['urls']['set'] = "%s%s/%s/set/" % (list_endpoint, '<ID>', n)
                elif isinstance(field, (ManyToManyRel, ManyToOneRel, ManyToManyField)):
                    schema['relations'][n]['urls']['add'] = "%s%s/%s/add/" % (list_endpoint, '<ID>', n)
                    schema['relations'][n]['urls']['remove'] = "%s%s/%s/remove/" % (list_endpoint, '<ID>', n)
                schema['relations'][n]['urls']['get'] = "%s%s/%s/" % (list_endpoint, '<ID>', n)
        return schema

    def hydrate(self, bundle):
        method_ref = self.settings.get('hydrate', None)
        if method_ref:
            method_callable = self._import_function(method_ref)
            bundle = method_callable(self, bundle)
        return bundle

    def dehydrate(self, bundle):
        method_ref = self.settings.get('dehydrate', None)
        if method_ref:
            method_callable = self._import_function(method_ref)
            bundle = method_callable(self, bundle)
        return bundle

    def prepend_urls(self):
        urls = super(CakeModelResource,self).prepend_urls()
        urls += [
            url(r"^(?P<resource_name>%s)/schema(?:/?)$" % (self._meta.resource_name), self.wrap_view('get_schema'), name="api_get_schema"),
            url(
                r"^(?P<resource_name>%s)/(?P<method>[^0-9][^/]*)/?$" % (self._meta.resource_name),
                self.wrap_view('dispatch_classmethod'), name="api_dispatch_classmethod"
            ),
            url(
                r"^(?P<resource_name>%s)/(?P<%s>.*?)/(?P<relation>[^/]+)/(?P<method>[^/]+)/?$" % (self._meta.resource_name, self._meta.detail_uri_name),
                self.wrap_view('dispatch_relation_method'), name="api_dispatch_relation_method"
            ),
            url(
                r"^(?P<resource_name>%s)/(?P<%s>.*?)/(?P<method>[^/]+)/?$" % (self._meta.resource_name, self._meta.detail_uri_name),
                self.wrap_view('dispatch_method'), name="api_dispatch_method"
            ),
        ]
        return urls

    def dispatch_classmethod(self, request, method=None, **kwargs):
        method_ref = self.settings.get('classmethods',{}).get(method, None)
        if not method_ref:
            raise NotFound("No such class method: %s" % method)
        method_callable = self._import_function(method_ref)
        ret = method_callable(self, request, method=method, **kwargs)
        return ret

    def dispatch_method(self, request, method=None, **kwargs):
        method_ref = self.settings.get('methods',{}).get(method, None)
        if method_ref:
            return self.dispatch_instancemethod(request, method=method, **kwargs)
        return self.dispatch_relation(request, relation=method, **kwargs)

    def dispatch_instancemethod(self, request, method=None, **kwargs):
        method_ref = self.settings.get('methods',{}).get(method, None)
        if not method_ref:
            raise NotFound("No such method: %s" % method)
        method_callable = self._import_function(method_ref)
        id = kwargs.get(self._meta.detail_uri_name)
        if id.isdigit():
            id = int(id)

        basic_bundle = self.build_bundle(request=request)
        try:
            obj = self.cached_obj_get(bundle=basic_bundle, **self.remove_api_resource_names(kwargs))
        except Exception, ex:
            raise NotFound("No such object %s%s/" % (self.get_list_endpoint(),id))

        ret = method_callable(self, request, obj, method=method, **kwargs)
        return ret

    def dispatch_relation(self, request, relation=None, **kwargs):
        from django.core.exceptions import FieldDoesNotExist
        try:
            field = self._meta.object_class._meta.get_field(relation)
        except FieldDoesNotExist, ex:
            raise BadRequest("No such relation: %s" % relation)
        id = kwargs.get(self._meta.detail_uri_name)
        if id.isdigit():
            id = int(id)

        basic_bundle = self.build_bundle(request=request)
        try:
            obj = self.cached_obj_get(bundle=basic_bundle, **self.remove_api_resource_names(kwargs))
        except Exception, ex:
            raise NotFound("No such object %s%s/" % (self.get_list_endpoint(),id))

        resource = self.get_resource_for_reference(relation)
        if not resource:
            raise BadRequest("The resource is not allowed for this relation: %s" % relation)

        if isinstance(field, (ForeignKey, OneToOneRel, OneToOneField)):
            try:
                foreign_object = getattr(obj, relation)
            except Exception, ex:
                raise NotFound("No such object %s%s/%s/" % (self.get_list_endpoint(), id, relation))
            if not foreign_object:
                raise NotFound("No such object %s%s/%s/" % (self.get_list_endpoint(), id, relation))
            resource.redirect_to_object(request, foreign_object)
        elif isinstance(field, ManyToManyField):
            resource.redirect_to_filter(request, {field.rel.name:obj.pk})
        elif isinstance(field, (ManyToManyRel, ManyToOneRel)):
            resource.redirect_to_filter(request, {field.remote_field.name:obj.pk})
        else:
            raise Exception("WTF?")

    def dispatch_relation_method(self, request, relation=None, method=None, **kwargs):
        # check the request method
        if not request.method.lower() == 'post':
            raise BadRequest("Only POST request for relation methods: %s" % relation)

        # check the relation presence
        from django.core.exceptions import FieldDoesNotExist
        try:
            field = self._meta.object_class._meta.get_field(relation)
        except FieldDoesNotExist, ex:
            raise BadRequest("No such relation: %s" % relation)

        # check the object presence and get an object
        id = kwargs.get(self._meta.detail_uri_name)
        if id.isdigit():
            id = int(id)

        basic_bundle = self.build_bundle(request=request)
        try:
            obj = self.cached_obj_get(bundle=basic_bundle, **self.remove_api_resource_names(kwargs))
        except Exception, ex:
            raise NotFound("No such object %s%s/" % (self.get_list_endpoint(),id))

        # check the relation method presence
        if isinstance(field, (ForeignKey, OneToOneRel, OneToOneField)):
            if not method == 'set':
                raise BadRequest("Only 'set' method allowed for this relation: %s" % relation)
        elif isinstance(field, (ManyToManyRel, ManyToOneRel, ManyToManyField)):
            if not method in ('add', 'remove'):
                raise BadRequest("Only 'add' and 'remove' methods allowed for this relation: %s" % relation)

        resource = self.get_resource_for_reference(relation)
        if not resource:
            raise BadRequest("The resource is not allowed for this relation: %s" % relation)

        try:
            arg = self.deserialize(request, request.body)
        except Exception, ex:
            raise BadRequest("Arguments deserialization error: %s" % ex)

        # check rights
        if isinstance(field, (ForeignKey, OneToOneField, ManyToManyField)):
            # For the original fields the update should be allowed
            basic_bundle.obj = obj
            if not self.authorized_update_detail(self.get_object_list(basic_bundle.request), basic_bundle):
                raise Unauthorized("Update not allowed while changing a relation: %s" % relation)
        elif isinstance(field, OneToOneRel):
            # For the foreign object the update should be allowed for the both, current and future objects
            foreign_bundle = resource.build_bundle(request=request)
            foreign_obj = None
            try:
                foreign_obj = getattr(obj, field.get_accessor_name())
            except Exception, ex:
                pass
            if foreign_obj:
                foreign_bundle.obj = foreign_obj
                if not resource.authorized_update_detail(resource.get_object_list(foreign_bundle.request), foreign_bundle):
                    raise Unauthorized("Update not allowed while changing a relation: %s" % relation)
            # Future object
            if arg:
                try:
                    foreign_obj = resource.cached_obj_get(bundle=foreign_bundle, **{
                        'resource_name': resource._meta.resource_name,
                        resource._meta.detail_uri_name: str(arg),
                    })
                except Exception, ex:
                    raise NotFound("No such object %s%s/" % (resource.get_list_endpoint(),id))
                if not resource.authorized_update_detail(resource.get_object_list(foreign_bundle.request), foreign_bundle):
                    raise Unauthorized("Update not allowed while changing a relation: %s" % relation)
        elif isinstance(field, (ManyToOneRel, ManyToManyRel)):
            # For the set of foreign objects the update should be allowed for all these objects for the both, add and del, requests
            foreign_bundle = resource.build_bundle(request=request)
            for pk in arg:
                try:
                    foreign_obj = resource.cached_obj_get(bundle=foreign_bundle, **{
                        'resource_name': resource._meta.resource_name,
                        resource._meta.detail_uri_name: str(pk),
                    })
                except Exception, ex:
                    raise NotFound("No such object %s%s/" % (resource.get_list_endpoint(),id))
                if not resource.authorized_update_detail(resource.get_object_list(foreign_bundle.request), foreign_bundle):
                    raise Unauthorized("Update not allowed while changing a relation: %s" % relation)

        # updating relation
        if isinstance(field, (ForeignKey, OneToOneField)):
            setattr(obj, field.get_attname(), arg)
            self.save(basic_bundle)
        elif isinstance(field, (ManyToManyField, ManyToManyRel, ManyToOneRel)):
            foreign_bundle = resource.build_bundle(request=request)
            mthd = getattr(getattr(obj, field.get_accessor_name()), method, None)
            if not mthd:
                raise BadRequest("The method '%s' not found for this relation: %s" % (method, relation))
            foreign_objects = []
            for pk in arg:
                try:
                    foreign_obj = resource.cached_obj_get(bundle=foreign_bundle, **{
                        'resource_name': resource._meta.resource_name,
                        resource._meta.detail_uri_name: str(pk),
                    })
                except Exception, ex:
                    raise NotFound("No such object %s%s/" % (resource.get_list_endpoint(),id))
                foreign_objects.append(foreign_obj)
            mthd(*foreign_objects)
        return HttpNoContent()

    def persistent_redirect_parameter_names(self):
        return ('format',)

    def persistent_redirect_parameters(self, **kwargs):
        ret = { k:kwargs[k] for k in kwargs if k in self.persistent_redirect_parameter_names() }
        return ret

    def redirect_to_id(self, request, id):
        raise ImmediateHttpResponse(response=self.redirect_to_id_response(request, id))

    def redirect_to_id_response(self, request, id):
        url = self.redirect_to_id_url(request, id)
        return HttpResponseRedirect(url)

    def redirect_to_id_url(self, request, id):
        args = self.persistent_redirect_parameters(**request.GET)
        redirect_args = urlencode([(k,v) for k in args for v in args[k]])
        return "%s%s/?%s" % (self.get_list_endpoint(), id, redirect_args)

    def redirect_to_filter(self, request, flt):
        raise ImmediateHttpResponse(response=self.redirect_to_filter_response(request, flt))

    def redirect_to_filter_response(self, request, flt):
        url = self.redirect_to_filter_url(request, flt)
        return HttpResponseRedirect(url)

    def redirect_to_filter_url(self, request, flt):
        args = self.persistent_redirect_parameters(**request.GET)
        args['filter'] = [json.dumps(flt)]
        redirect_args = urlencode([(k,v) for k in args for v in args[k]])
        return "%s?%s" % (self.get_list_endpoint(), redirect_args)

    def redirect_to_object(self, request, obj):
        return self.redirect_to_id(request, obj.id)

    def redirect_to_object_url(self, request, obj):
        return self.redirect_to_id_url(request, obj.id)

    def redirect_to_object_response(self, request, obj):
        return self.redirect_to_id_response(request, obj.id)
