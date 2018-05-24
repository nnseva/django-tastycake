from tastypie.exceptions import BadRequest, NotFound, ImmediateHttpResponse
from tastypie.authentication import SessionAuthentication
from tastypie.authorization import ReadOnlyAuthorization

from access_tastypie.authorization import AccessAuthorization

from django.http import HttpResponseRedirect
from urlparse import urlparse

def contenttype_find_by_url(self, request, method=None, **kwargs):
    url = request.GET.get('url',None)
    if not url:
        raise BadRequest("No URL parameter")

    scheme, netloc, path, params, query, fragment = urlparse(url)
    path_parts = path.split('/')
    if len(path_parts) < 3:
        raise BadRequest("Bad URL parameter: %s" % url)

    app_name = path_parts[1]
    model_name = path_parts[2]

    self.redirect_to_filter(request, {'app_label':app_name, 'model':model_name})

def contenttype_url(self, request, obj, method=None, **kwargs):
    return {"url":'/'.join(["",obj.app_label, obj.model])}

def authentication(model):
    return SessionAuthentication()

def authorization(model):
    return AccessAuthorization()
