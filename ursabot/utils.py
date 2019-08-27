# Copyright 2019 RStudio, Inc.
# All rights reserved.
#
# Use of this source code is governed by a BSD 2-Clause
# license that can be found in the LICENSE_BSD file.

import copy
import platform
import pathlib
import fnmatch
import itertools
from functools import wraps
from typing import ClassVar

import distro
import toolz
import typeguard
from twisted.internet import defer
from buildbot.util import httpclientservice
from buildbot.util.logger import Logger

__all__ = [
    'ensure_deferred',
    'read_dependency_list',
    'AnyOf',
    'AllOf',
    'Matching',
    'Glob',
    'Filter',
    'Annotable',
    'Merge',
    'Extend',
    'HTTPClientService',
    'GithubClientService',
]

log = Logger()


def ensure_deferred(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        return defer.ensureDeferred(result)

    return wrapper


def read_dependency_list(path):
    """Parse plaintext files with comments as list of dependencies"""
    path = pathlib.Path(path)
    lines = (l.strip() for l in path.read_text().splitlines())
    return [l for l in lines if not l.startswith('#')]


# Utilities for validation and declarative definitions


class Marker:

    def resolve(self, parent):
        raise NotImplementedError()


class Merge(Marker, dict):

    def resolve(self, parent):
        if not isinstance(parent, dict):
            raise TypeError('Merge marker can only be used on a '
                            'parent field with dictionary type')
        return {**parent, **self}


class Extend(Marker, list):

    def resolve(self, parent):
        if not isinstance(parent, list):
            raise TypeError('Extend marker can only be used on a '
                            'parent field with list type')
        return parent + self


class MISSING:
    pass


class Field:

    __slots__ = ('name', 'type', 'default')

    def __init__(self, name, type, default):
        self.name = name
        self.type = type
        self.default = default
        if default is not MISSING:
            self.validate(default)

    def with_default(self, new_default):
        return Field(name=self.name, type=self.type, default=new_default)

    def validate(self, value):
        typeguard.check_type(self.name, value, self.type)


class AnnotableMeta(type):

    def __new__(metacls, clsname, bases, attrs):
        class_anns, instance_anns = {}, {}
        for name, type in attrs.get('__annotations__', {}).items():
            if getattr(type, '__origin__', None) is ClassVar:
                class_anns[name] = type.__args__[0]
            else:
                instance_anns[name] = type

        class_fields, instance_fields = {}, {}
        for base in reversed(bases):
            instance_fields.update(getattr(base, '__fields__', {}))
            class_fields.update(getattr(base, '__class_fields__', {}))

        attrs['__fields__'] = metacls._update_fields(
            instance_anns, instance_fields, attrs
        )
        attrs['__class_fields__'] = metacls._update_fields(
            class_anns, class_fields, attrs
        )

        return super().__new__(metacls, clsname, bases, attrs)

    @classmethod
    def _update_fields(metacls, annotations, fields, attrs):
        # fields with new default values
        new_defaults = {}
        for name, field in fields.items():
            if name in attrs:
                default = attrs[name]
                if isinstance(default, Marker):
                    default = default.resolve(field.default)
                new_defaults[name] = field.with_default(default)

        # newly annotated fields
        new_fields = {}
        for name, type in annotations.items():
            default = attrs.get(name, MISSING)
            new_fields[name] = Field(name, type=type, default=default)

        return {**fields, **new_defaults, **new_fields}


class Annotable(metaclass=AnnotableMeta):

    def __init__(self, **kwargs):
        # TODO(kszucs): collect errors
        for name, field in self.__fields__.items():
            try:
                value = kwargs[name]
                if isinstance(value, Marker):
                    value = value.resolve(field.default)
            except KeyError:
                if field.default is MISSING:
                    raise TypeError(
                        f'missing required keyword-only argument: {name}'
                    )
                else:
                    value = copy.copy(field.default)

            field.validate(value)
            setattr(self, name, value)

    def __repr__(self):
        classname = self.__class__.__name__
        address = hex(id(self))
        values = ' '.join(f'{k}={v}' for k, v in self._values())
        return f'<{classname} {values} object at {address}>'

    def __eq__(self, other):
        return (
            type(self) == type(other) and
            self.asdict() == other.asdict()
        )

    def _values(self):
        for name in self.__fields__.keys():
            yield (name, getattr(self, name))

    def asdict(self):
        return dict(self._values())


# Utilities for filtering


def Has(*needles):
    return lambda haystack: set(needles).issubset(set(haystack))


def InstanceOf(cls):
    return lambda value: isinstance(value, cls)


def Matching(pattern):
    if pattern is None:
        return lambda v: True
    else:
        return lambda v: fnmatch.fnmatch(v, pattern)


def Glob(pattern):
    return lambda vs: fnmatch.filter(vs, pattern)


def AnyOf(*validators):
    def check(value):
        for validator in validators:
            if callable(validator):
                if validator(value):
                    return True
            else:
                if value == validator:
                    return True
        return False
    return check


def AllOf(*validators):
    def check(value):
        for validator in validators:
            if callable(validator):
                if not validator(value):
                    return False
            else:
                if value != validator:
                    return False
        return True
    return check


def Filter(**kwargs):
    def check(obj):
        for attr, validator in kwargs.items():
            value = getattr(obj, attr)
            if callable(validator):
                if not validator(value):
                    return False
            else:
                if value != validator:
                    return False
        return True
    return check


# Platform definition

# from enum import Enum
#
#
# class Arch(str, Enum):
#     AMD64 = 'AMD64'
#     ARM64V8 = 'ARM64v8'
#     ARM32V7 = 'ARM32v7'
#
#
# class System(str, Enum):
#     WINDOWS = 'Windows'
#     DARWIN = 'Darwin'
#     LINUX = 'Linux'
#
#
# class Linux(str, Enum):
#     ALPINE = 'Alpine'
#     DEBIAN = 'Debian'
#     UBUNTU = 'Ubuntu'
#     CENTOS = 'CentOS'
#     FEDORA = 'Fedora'
#
#     @property
#     def system(self):
#         return System.LINUX
#
#
# class Darwin(str, Enum):
#     MACOS = 'macOS'
#
#     @property
#     def system(self):
#         return System.DARWIN
#
#
# class Windows(str, Enum):
#     WINDOWS = 'Windows'
#
#     @property
#     def system(self):
#         return System.WINDOWS
#


class Platform:

    __slots__ = ('arch', 'system', 'distro', 'version', 'codename')

    _architectures = {
        'x86_64': 'amd64'
    }
    _systems = {
        'debian': 'linux',
        'ubuntu': 'linux',
        'centos': 'linux',
        'alpine': 'linux',
        'fedora': 'linux',
        'macos': 'darwin',
        'windows': 'windows'
    }

    def __init__(self, arch, distro, version, system=None, codename=None):
        arch = self._architectures.get(arch, arch)
        if arch not in {'amd64', 'arm64v8', 'arm32v7'}:
            raise ValueError(f'invalid architecture `{arch}`')

        system = system or self._systems.get(distro)
        if system not in {'linux', 'darwin', 'windows'}:
            raise ValueError(f'invalid system `{system}`')

        self.arch = arch
        self.system = system
        self.distro = distro
        self.version = version
        self.codename = codename

    def title(self):
        return f'{self.arch.upper()} {self.distro.capitalize()} {self.version}'

    def __eq__(self, other):
        return (
            self.arch == other.arch and
            self.system == other.system and
            self.distro == other.distro and
            self.version == other.version
        )

    def __hash__(self):
        return hash((self.arch, self.system, self.distro, self.version))

    def __str__(self):
        arch = self.arch or 'unknown'
        distro = self.distro or 'unknown'
        version = self.version or 'unknown'
        return f'{arch}-{distro}-{version}'

    def __repr__(self):
        return (f'<Platform arch={self.arch} system={self.system} '
                f'distro={self.distro} version={self.version} at {id(self)}>')

    @classmethod
    def detect(cls):
        system = platform.system().lower()
        if system == 'windows':
            dist = 'windows'
            version, *_ = platform.win32_ver()
            codename = None
        elif system == 'darwin':
            dist = 'macos'
            version, *_ = platform.mac_ver()
            codename = None
        else:
            dist = distro.id()
            version = distro.version()
            codename = distro.codename()

        return cls(
            arch=platform.machine(),
            system=system,
            distro=dist,
            version=version,
            codename=codename
        )


# Buildbot utilities


class HTTPClientService(httpclientservice.HTTPClientService):

    PREFER_TREQ = True

    def _prepareRequest(self, ep, kwargs):
        # XXX: originally the default headers and the headers received as an
        # arguments were merged in the wrong order
        default_headers = self._headers or {}
        headers = kwargs.pop('headers', None) or {}

        url, kwargs = super()._prepareRequest(ep, kwargs)
        kwargs['headers'] = {**default_headers, **headers}

        return url, kwargs


class GithubClientService(HTTPClientService):

    def __init__(self, *args, tokens, rotate_at=1000, max_retries=5,
                 headers=None, **kwargs):
        assert rotate_at < 5000
        tokens = list(tokens)
        self._tokens = itertools.cycle(tokens)
        self._n_tokens = len(tokens)
        self._rotate_at = rotate_at
        self._max_retries = max_retries
        headers = headers or {}
        headers.setdefault('User-Agent', 'Buildbot')
        super().__init__(*args, headers=headers, **kwargs)

    def startService(self):
        self._set_token(next(self._tokens))
        return super().startService()

    def _set_token(self, token):
        if self._headers is None:
            self._headers = {}
        self._headers['Authorization'] = f'token {token}'

    @ensure_deferred
    async def rate_limit(self, token=None):
        headers = {}
        if token is not None:
            headers['Authorization'] = f'token {token}'

        response = await self._doRequest('get', '/rate_limit', headers=headers)
        data = await response.json()

        return data['rate']['remaining']

    @ensure_deferred
    async def rotate_tokens(self):
        # try each token, query its rate limit
        # if none of them works log and sleep
        for token in toolz.take(self._n_tokens, self._tokens):
            remaining = await self.rate_limit(token)

            if remaining > self._rotate_at:
                return self._set_token(token)

    @ensure_deferred
    async def _do_request(self, method, endpoint, **kwargs):
        for attempt in range(self._max_retries):
            response = await self._doRequest(method, endpoint, **kwargs)
            headers, code = response.headers, response.code

            if code // 100 == 4:
                if code == 401:
                    # Unauthorized: bad credentials
                    reason = 'bad credentials (401)'
                elif code == 403:
                    # Forbidden: exceeded rate limit or forbidden access
                    reason = 'exceeded rate limit or forbidden access (403)'
                elif code == 404:
                    # Requests that require authentication will return 404 Not
                    # Found, instead of 403 Forbidden, in some places. This is
                    # to prevent the accidental leakage of private repositories
                    # to unauthorized users.
                    reason = 'resource not found (404)'
                else:
                    reason = f'status code {code}'

                log.info(f'Failed to fetch endpoint {endpoint} because of '
                         f' {reason}. Retrying with the next token.')
                await self.rotate_tokens()
            else:
                if headers.hasHeader('X-RateLimit-Remaining'):
                    values = headers.getRawHeaders('X-RateLimit-Remaining')
                    remaining = int(toolz.first(values))
                    if remaining <= self._rotate_at:
                        log.info('Remaining rate limit has reached the '
                                 'rotation limit, switching to the next '
                                 'token.')
                        await self.rotate_tokens()
                break

        return response

    def get(self, endpoint, **kwargs):
        return self._do_request('get', endpoint, **kwargs)

    def put(self, endpoint, **kwargs):
        return self._do_request('put', endpoint, **kwargs)

    def delete(self, endpoint, **kwargs):
        return self._do_request('delete', endpoint, **kwargs)

    def post(self, endpoint, **kwargs):
        return self._do_request('post', endpoint, **kwargs)
