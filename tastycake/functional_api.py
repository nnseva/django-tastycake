# TODO!!!
class FunctionalApi(BaseApi):
    def __init__(self,serializer_class=Serializer,settings=None):
        super(FunctionalApi,self).__init__(serializer_class=serializer_class)
        if settings is None:
            settings = getattr(settings,'TASTYCAKE',{})
        self.settings = settings
        self.suburls = {
            "schema": self.schema,
        }
        for k in self.settings:
            if hasattr(self.settings[k],"get") and self.settings[k].get("api",None):
                api = self.settings[k].get("api",None)
                if callable(api):
                    api_callable = api
                elif isinstance(api,basestring):
                    try:
                        module_name, name = api.rsplit(".",1)
                    except Exception, ex:
                        logger.error("Wrong 'api' value in settings: %s" % api)
                        continue
                    module = import_module(module_name)
                    api_callable = getattr(module, name, None)
                    if not api_callable:
                        logger.error("Wrong 'api' value in settings, no such member: %s" % api)
                        continue
                    if not callable(api_callable):
                        logger.error("Wrong 'api' value in settings, not a callable: %s" % api)
                        continue
                else:
                    logger.error("Wrong 'api' value in settings, should be a callable or a string: %s" % api)
                    continue
                self.suburls[k] = api_callable(serializer_class=serializer_class,settings=settings[k])

    def get_schema(self):
        return {
        }

