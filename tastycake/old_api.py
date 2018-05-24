class Api(BaseApi):
    def __init__(self,serializer_class=Serializer):
        super(Api,self).__init__(serializer_class=serializer_class)

        self.settings = {'v1':{}}
        if hasattr(settings,'TASTYCAKE'):
            self.settings = settings.TASTYCAKE

    @property
    def urls(self):
        return [
            url(r'^$',self.wrap_view(self.get_versions)),
            url(r'^(?P<version>[^/]*)/?$',self.wrap_view(self.dispatch_version)),
            url(r'^(?P<version>[^/]*)/(?P<app>[^/]*)/?$',self.wrap_view(self.dispatch_app)),
            url(r'^(?P<version>[^/]*)/(?P<app>[^/]*)/(?P<model>[^/]*)/?$',self.wrap_view(self.dispatch_instances)),
            url(r'^(?P<version>[^/]*)/(?P<app>[^/]*)/(?P<model>[^/]*)/schema/?$',self.wrap_view(self.dispatch_model)),
            url(r'^(?P<version>[^/]*)/(?P<app>[^/]*)/(?P<model>[^/]*)/(?P<pk>[^/]*)(?P<subpath>/.*)?$',self.wrap_view(self.dispatch_instance)),
        ]


    def get_versions(self,request,**kw):
        versions = [v for v in self.settings]
        return versions

    def dispatch_version(self,request,version=None,**kw):
        self._check_method(request,['get'])
        if not version in self.settings:
            raise NotFound("No such version: %s" % version)
        settings = self.settings[version]
        ret = {
            'version':version,
            'apps': self._get_applications(request, version, settings)
        }

        for k in settings:
            if k in ('name','date','description','deprecated','readonly'):
                ret[k] = settings[k]

        return ret

    def _get_applications(self, request, version, settings):
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


    def _get_application(self, request, version, app, settings):
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

        ret['models'] = self._get_application_models(request, version, app, settings)

        return ret

    def _get_application_models(self, request, version, app, settings):
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

    def dispatch_model(self,request,version=None,app=None,model=None,**kw):
        self._check_method(request,['get'])
        if not version in self.settings:
            raise NotFound("No such version: %s" % version)
        settings = self.settings[version]
        return self._get_application_model(request, version, app, model, settings)

    def _get_application_model(self, request, version, app, model, settings):
        settings_exclude = settings.get('exclude',{})

        if app in settings_exclude:
            raise NotRegistered("No such application label: %s" % app)

        if "%s.%s" % (app,model) in settings_exclude:
            raise NotRegistered("No such model: %s.%s" % (app,model))

        settings_app = settings.get('apps',{}).get(app,{})
        settings_model = settings_app.get('models',{}).get(model,{})

        ret = {}

        try:
            config = apps.get_app_config(app)
            model_class = config.get_model(model)
            ret = {
                'verbose_name':model_class._meta.verbose_name,
                'verbose_name_plural':model_class._meta.verbose_name_plural,
            }
        except LookupError:
            pass

        if not ret and not settings_model:
            raise NotRegistered("No such model: %s.%s" % (app,model))

        for n in settings_model:
            if n in (
                'verbose_name',
                'verbose_name_plural',
            ):
                ret[n] = settings_model[n]

        return ret

    def dispatch_instances(self,request,version=None,app=None,model=None,**kw):
        return {'themodel':model,'theversion':version,'theapp':app}

    def dispatch_instance(self,request,version=None,app=None,model=None,pk=None,subpath=None,**kw):
        return {'pk':pk,'subpath':subpath,'themodel':model,'theversion':version,'theapp':app}
