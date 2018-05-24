[![Build Status](https://travis-ci.org/nnseva/django-tastycake.svg?branch=master)](https://travis-ci.org/nnseva/django-tastycake)

# django-tastycake

The django-tastycake package provides a simplyfied automated resource builder to create
a WEB API without coding at all, or with a minimal code.

It is based on the [Django-Tastypie](https://django-tastypie.readthedocs.io/en/latest/) package
but totaly ignores it's model-oriented part. The only base functionality is used.

The package also contains bits to be compatible with the [Django-Access](https://github.com/nnseva/django-access) package.

## Installation

*Stable version* from the PyPi package repository
```bash
pip install django-tastycake
```

*Last development version* from the GitHub source version control system
```bash
pip install git+git://github.com/nnseva/django-tastycake.git
```

## Configuration

Include the `tastypie` and `tastycake` applications into the `INSTALLED_APPS` list, like:

```python
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    ...
    'tastypie',
    'tastycake',
    ...
]
```

You also can install `access` application, if you would like to use access rules
defined by the [Django-Access](https://github.com/nnseva/django-access) package
instead of standard Django access authorization, like:

```python
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    ...
    'tastypie',
    'access',
    'tastycake',
    ...
]
```

Install the only one URL to the `urls.py` file, like:

```python
...
urlpatterns += [
    url(r'^api/', tastycake.api.urls),
]
```

## Using

### API calls

The installed API URL is self-described. It supports all tastypie base serialization functions and return value type autodetection.

The root URL lists versions of the API defined by the programmer. If no any versions are defined, the default `v1` is returned.

The version URL like `api/v1/` enlists all available packages. The packages list is mostly inherited from the available app_label values.

The package list like 'api/v1/auth` enlists all available resources in the package. The resources list is mostly inherited from the available models list.

Every model resource like `api/v1/auth/user` has several standard operations:

- instance list is available using GET request for the base resource path like `api/v1/auth/user`
- the particular instance details access is available using GET request for the instance resource path like `api/v1/auth/user/123` identified by the instance primary key
- instance creation is available using POST request for the base resource path like `api/v1/auth/user`
- instance patching is available using PATCH request for the instance resource path like `api/v1/auth/user/123`
- instance deletion is available using DELETE request for the instance resource path like `api/v1/auth/user/123`
- related instance access is performed by the relation name following the instance resource path like `api/v1/auth/user/123/groups`
- if the relation is defined as OneToOneField, or a ForeignKey, the related instance is available as an only instance like by the instance resource path
- if the relation is defined as the opposite side of the ForeignKey, the related instances are available as an instance list

All GET requests support `field` parameter. The `field` parameter can be used multiple times and is used
to restrict request by the only defined field list. Absent `field` parameter for the details request means all available simple fields,
and for the list request means the only primary key (id) field value.

The `field` parameter may be used only for the simple fields.

Such a way, the following requests will return:

```url
/api/v1/auth/user
```

will return something like

```JSON
{
    "meta": {
        "limit": 20,
        "next": null,
        "offset": 0,
        "previous": null,
        "total_count": 3
    }
    "objects":[
        1,2,3
    ]
```

```url
/api/v1/auth/user/1
```

will return something like

```JSON
{
    "username": "root",
    "first_name": null,
    "last_name": null,
    "email": null,
    "is_superuser": true,
    "is_active": true,
    "is_staff": true,
    "date_joined": "2017-11-11T11:11:11.1111",
    "last_login": "2017-11-11T22:22:22.2222",
}
```

```url
/api/v1/auth/user?field=id&field=username
```

will return something like

```JSON
{
    "meta": {
        "limit": 20,
        "next": null,
        "offset": 0,
        "previous": null,
        "total_count": 3
    }
    "objects":[
        {
            "id": 1,
            "username": "root"
        },
        {
            "id": 2,
            "username": "stem"
        },
        {
            "id": 3,
            "username": "leaf"
        },
    ]
```

```url
/api/v1/auth/user/1?field=id&field=username
```

will return something like

```JSON
{
    "id": 1,
    "username": "root",
}
```
