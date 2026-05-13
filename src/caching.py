


def cache(func):
    values = {}
    def wrapper(*args, **kwargs):
        key = hash((args, tuple(sorted(kwargs.items()))))
        if key in values:
            return values[key]

        value = func(*args, **kwargs)
        values[key] = value
        return value

    return wrapper

class Pool:
    def __init__(self, creator=None) -> None:
        self.pool = {}
        self.creator = creator

    def add(self, object, *key):
        if not key:
            raise ValueError('Must provide a key to Pool.add()')

        if len(key) == 1:
            key = key[0]

        self.pool[key] = object

        return object

    def __getitem__(self, key):
        if key in self.pool:
            return self.pool[key]

        if not self.creator:
            raise ValueError(f'Key not found in pool and no creator has been defined: {key}')

        return self.add(self.creator(key), key)

    def __len__(self):
        return len(self.pool)

    def __contains__(self, key):
        return key in self.pool

    def __repr__(self) -> str:
        return f'Pool({self.pool})'


