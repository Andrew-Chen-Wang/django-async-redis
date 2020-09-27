class BaseSerializer:
    def __init__(self, options):
        ...

    def dumps(self, value):
        raise NotImplementedError

    def loads(self, value):
        raise NotImplementedError
