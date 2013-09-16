# Copyright (C) 2009-2013, Quentin "mefyl" Hocquet
#
# This software is provided "as is" without warranty of any kind,
# either expressed or implied, including but not limited to the
# implied warranties of fitness for a particular purpose.
#
# See the LICENSE file for more information.

_OS = __import__('os')
import atexit
import drake.debug
import hashlib
import inspect
import itertools
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import types
from copy import deepcopy
from drake.sched import Coroutine, Scheduler
from drake.enumeration import Enumerated

def _scheduled():
  return Coroutine.current and \
    Coroutine.current._Coroutine__scheduler


class Profile:

    def __init__(self, name):
        self.__calls = 0
        self.__name = name
        self.__time = 0
        atexit.register(self.show)

    def profile(self):
        return ProfileInstance(self)

    def show(self):
        print(self)

    def __str__(self):
        return '%s: called %s time, %s seconds.' % (self.__name,
                                                    self.__calls,
                                                    self.__time)

class ProfileInstance:

    def __init__(self, parent):
        self.__parent = parent
        self.__time  = 0
        self.__time  = None

    def __enter__(self):
        self.__time = time.time()

    def __exit__(self, *args):
        self.__parent._Profile__calls += 1
        t = time.time() - self.__time
        self.__parent._Profile__time  += t


class Exception(Exception):
    """Base type for any exception thrown within drake."""
    pass


class NodeRedefinition(Exception):

    """Thrown when a node is redefined.

    In most cases, this exception will not be seen by the user
    since all primitives (even node constructors) will return the
    existing node if it exists."""

    def __init__(self, name_absolute):
        """Build a redefinition exception.

        name_absolute -- the absolute name of the redefined node."""
        Exception.__init__(self)
        self.__name = name_absolute

    def __str__(self):
        """Description, with the redefined node name."""
        return 'node redefinition: %s' % self.__name

    def name(self):
        """The absolute name of the redefined node.

        See Node.name_absolute."""
        return self.__name

class NoBuilder(Exception):

    """Node raised when a Node builder is missing.

    Raised when a Nodehas no builder and its associated file does not
    exist.
    """

    def __init__(self, node):
        """Build a NoBuilder exception.

        node -- the node whose builder is missing.
        """
        self.__node = node
        Exception.__init__(self,
                           'no builder to make %s' % self.__node)

_RAW = 'DRAKE_RAW' in _OS.environ
_SILENT = 'DRAKE_SILENT' in _OS.environ


class Path(object):

    """Node names, similar to a filesystem path."""

    separator = '/'

    if platform.system() == 'Windows':
        separator = '\\'

    def __init__(self, path):
        """Build a path.

        path -- The path, as a string or an other Path.
        """
        self.virtual = False
        self.__absolute = False
        if path.__class__ == list:
            self.__path = path
        elif path.__class__ == Path:
            self.__path = deepcopy(path.__path)
            self.__absolute = path.__absolute
            self.virtual = path.virtual
        else:
            if not path:
                self.__path = []
            elif platform.system() == 'Windows':
                if path[:2] == '//' or path[:2] == '\\\\':
                    path = path[2:]
                    self.virtual = True
                self.__path = re.split(r'/|\\', path)
                slash = self.__path[0] == ''
                volume = re.compile('^[A-Z]:').match(self.__path[0])
                self.__absolute = bool(slash or volume)
            else:
                if path[:2] == '//':
                    path = path[2:]
                    self.virtual = True
                self.__path = path.split('/')
                self.__absolute = self.__path[0] == ''
        if len(self.__path) > 1 and self.__path[-1] == '':
            self.__path = self.__path[:-1]

    def absolute(self):
        """Whether this path is absolute.

        >>> Path('.').absolute()
        False
        >>> Path('foo/bar').absolute()
        False
        >>> Path('/').absolute()
        True
        >>> Path('/foo').absolute()
        True
        """
        return self.__absolute

    def remove(self, err = False):
        """Remove the target file.

        err -- Whether this is an error for non-existent file.

        No-op if the file does not exist, unless err is true.

        >>> p = Path('/tmp/.drake.foo')
        >>> p.touch()
        >>> p.exists()
        True
        >>> p.remove()
        >>> p.exists()
        False
        >>> p.touch()
        >>> p.remove(True)
        >>> p.remove(True)
        Traceback (most recent call last):
            ...
        drake.Exception: Path does not exist: /tmp/.drake.foo
        """
        try:
            _OS.remove(str(self))
        except OSError as e:
          if e.errno == 2:
            if err:
              raise Exception('Path does not exist: %s' % str(self))
          elif e.errno == 21:
            shutil.rmtree(str(self))
          else:
            raise

    def __extension_get(self):
        parts = self.__path[-1].split('.')
        if len(parts) > 1:
            return '.'.join(parts[1:])
        else:
            return ''

    def __extension_set(self, value):
        parts = self.__path[-1].split('.')
        if len(parts) > 1:
            if value == '':
                parts = [parts[0]]
            else:
                parts = [parts[0], value]
            self.__path[-1] = '.'.join(parts)
        else:
            if value != '':
                self.__path[-1] += '.%s' % value
        return value

    extension = property(
        fget = __extension_get, fset = __extension_set,
        doc = """Extension of the file name.

        The extension is the part after the first dot of the basename,
        or the empty string if there are no dot.

        >>> Path('foo.txt').extension
        'txt'
        >>> Path('foo.tar.bz2').extension
        'tar.bz2'
        >>> Path('foo').extension
        ''
        >>> p = Path('foo')
        >>> p
        Path("foo")
        >>> p.extension = 'txt'
        >>> p
        Path("foo.txt")
        >>> p.extension = 'tar.bz2'
        >>> p
        Path("foo.tar.bz2")
        """)

    def extension_strip_last_component(self):
        """Remove the last dot and what follows from the basename.

        Does nothing if there is no dot.

        >>> p = Path('foo.tar.bz2')
        >>> p
        Path("foo.tar.bz2")
        >>> p.extension_strip_last_component()
        >>> p
        Path("foo.tar")
        >>> p.extension_strip_last_component()
        >>> p
        Path("foo")
        >>> p.extension_strip_last_component()
        >>> p
        Path("foo")
        """
        self.__extension_set('.'.join(self.extension.split('.')[:-1]))

    def __str__(self):
        """The path as a string, adapted to the underlying OS."""
        prefix = self.virtual and '//' or ''
        if not self.__path:
            body = '.'
        else:
            body = self.separator.join(self.__path)
        return prefix + body

    def __repr__(self):
        """Python representation."""
        return 'Path(\"%s\")' % str(self)

    def __lt__(self, rhs):
        """Arbitrary comparison.

        >>> Path('foo') < Path('foo')
        False
        >>> (Path('foo') < Path('bar')) ^ (Path('bar') < Path('foo'))
        True
        """
        return str(self) < str(rhs)

    def __hash__(self):
        """Hash value.

        >>> hash(Path('foo')) == hash(Path('foo'))
        True
        """
        return hash(str(self))

    def exists(self):
        """Whether the designed file or directory exists.

        >>> p = Path('/tmp/.drake.foo')
        >>> p.touch()
        >>> p.exists()
        True
        >>> p.remove()
        >>> p.exists()
        False
        """
        if _OS.path.islink(str(self)):
            return True
        return _OS.path.exists(str(self))

    @property
    def executable(self):
        """Whether the designed file is executable by the user."""
        return _OS.access(str(self), _OS.X_OK)

    def is_file(self):
        """Whether the designed file exists and is a regular file.

        >>> p = Path('/tmp/.drake.foo')
        >>> p.touch()
        >>> p.is_file()
        True
        >>> p.remove()
        >>> p.is_file()
        False
        >>> p.mkpath()
        >>> p.exists()
        True
        >>> p.is_file()
        False
        >>> p.remove()
        """
        return _OS.path.isfile(str(self))

    def basename(self):
      """The filename part of the path.

      This is the path without the dirname. Throws if the path has
      no components.


      >>> Path('foo/bar/baz').basename()
      Path("baz")
      >>> Path('').basename()
      Traceback (most recent call last):
          ...
      drake.Exception: Cannot take the basename of an empty path.
      """
      if not self.__path:
        raise Exception('Cannot take the basename of an empty path.')
      return Path(self.__path[-1:])

    def dirname(self):
      """The directory part of the path.

      This is the path without the basename. Throws if the path has
      no components.

      >>> Path('foo/bar/baz').dirname()
      Path("foo/bar")
      >>> Path('foo').dirname()
      Path(".")
      >>> Path('').dirname()
      Traceback (most recent call last):
          ...
      drake.Exception: Cannot take the dirname of an empty path.
      """
      if not self.__path:
        raise Exception('Cannot take the dirname of an empty path.')
      res = Path(self.__path[0:-1])
      res.__absolute = self.__absolute
      return res

    def empty(self):
        """Whether the path is empty.

        >>> Path('').empty()
        True
        >>> Path('foo').empty()
        False
        """
        return len(self.__path) == 0

    def touch(self):
        """Create the designed file if it does not exists.

        Creates the parent directories if needed first.

        >>> Path('/tmp/.drake').remove()
        >>> p = Path('/tmp/.drake/.sub/.foo')
        >>> p.touch()
        >>> p.exists()
        True

        If the file does exist, this is a no-op.

        >>> path = Path('/tmp/.drake.touch.exists')
        >>> with open(str(path), 'w') as f:
        ...   print('foobar', file = f)
        >>> path.touch()
        >>> with open(str(path), 'r') as f:
        ...   print(f.read(), end = '')
        foobar
        """
        if not self.dirname().empty():
            self.dirname().mkpath()
        if not _OS.path.exists(str(self)):
            open(str(self), 'w').close()

    def mkpath(self):
        """Create the designed directory.

        Creates the parent directories if needed first.

        >>> Path('/tmp/.drake').remove()
        >>> p = Path('/tmp/.drake/.sub/')
        >>> p.mkpath()
        >>> p.exists()
        True
        """
        if not _OS.path.exists(str(self)):
            _OS.makedirs(str(self))

    def __eq__(self, rhs):
        """Whether self equals rhs.

        Pathes are equals if they have the same components and
        absoluteness.

        >>> Path('foo/bar') == Path('foo/bar')
        True
        >>> Path('foo/bar') == Path('foo')
        False
        >>> Path('foo/bar') == Path('bar/foo')
        False
        >>> Path('foo/bar') == Path('/foo/bar')
        False
        >>> Path('/foo/bar') == Path('/foo/bar')
        True
        >>> Path('') == Path('.')
        True
        """
        if rhs.__class__ != Path:
            rhs = Path(rhs)
        def neutralize(p):
            if not p:
                return ['.']
            else:
                return p
        return neutralize(self.__path) == neutralize(rhs.__path)

    def __truediv__(self, rhs):
      """The concatenation of self and rhs.

      rhs -- the end of the new path, as a Path or a string.

      >>> Path('foo/bar') / Path('bar/baz')
      Path("foo/bar/bar/baz")
      >>> Path('foo/bar') / 'baz'
      Path("foo/bar/baz")
      >>> Path('.') / 'baz'
      Path("baz")

      One cannot concatenate an absolute path.

      >>> Path('foo') / Path('/absolute')
      Traceback (most recent call last):
          ...
      drake.Exception: Cannot concatenate an absolute path: Path("/absolute").
      """
      rhs = Path(rhs)
      if rhs.absolute():
        raise Exception(
            'Cannot concatenate an absolute path: %s.' % repr(rhs))
      if self == '.':
        return rhs
      if rhs == Path('.'):
        return Path(self)
      res = Path(self)
      res.__path += rhs.__path
      return res

    def strip_prefix(self, rhs):
        """Remove rhs prefix from self.

        rhs -- the prefix to strip, as a Path or a string.

        >>> p = Path('foo/bar/baz/quux')
        >>> p
        Path("foo/bar/baz/quux")
        >>> p.strip_prefix("foo/bar")
        >>> p
        Path("baz/quux")
        >>> p = Path('/foo/bar/baz')
        >>> p.absolute()
        True
        >>> p.strip_prefix('/foo')
        >>> p
        Path("bar/baz")
        >>> p.absolute()
        False

        Rewinds if rhs is not a prefix of self.

        >>> p.strip_prefix("quux")
        >>> p
        Path("../bar/baz")
        """
        if (not isinstance(rhs, Path)):
            rhs = Path(rhs)
        rhs = list(rhs.__path)
        path = self.__path
        while len(rhs) and len(path) and path[0] == rhs[0]:
          rhs = rhs[1:]
          path = path[1:]
        # FIXME: naive if rhs contains some '..'
        self.__path = ['..'] * len(rhs) + path
        if not self.__path:
          self.__path = ['.']
        self.__absolute = self.__path[0] == ''

    def __len__(self):
        return len(self.__path)

    def strip_suffix(self, rhs):
        """Remove rhs suffix from self.

        rhs -- the suffix to strip, as a Path or a string.

        >>> p = Path('foo/bar/baz/quux')
        >>> p
        Path("foo/bar/baz/quux")
        >>> p.strip_suffix("baz/quux")
        >>> p
        Path("foo/bar")

        Throws if rhs is not a prefix of self.

        >>> p.strip_suffix("quux")
        Traceback (most recent call last):
            ...
        drake.Exception: quux is not a suffix of foo/bar
        """
        if (not isinstance(rhs, Path)):
            rhs = Path(rhs)
        if self.__path[-len(rhs.__path):] != rhs.__path:
            raise Exception("%s is not a suffix of %s" % (rhs, self))
        self.__path = self.__path[0:-len(rhs.__path):]
        if not self.__path:
            self.__path = ['.']
        self.__absolute = self.__path[0] == ''

    @classmethod
    def cwd(self):
        return Path(_OS.getcwd())

    def list(self):
        return _OS.listdir(str(self))

_CACHEDIR = Path('.drake')

_DEPFILE_BUILDER = Path('drake.Builder')

class DepFile:

    """File to store dependencies of a builder and their hash.

    To determine whether a builder should be executed, Drake has to
    check whether any of its sources has changed since the last
    build. This is done by comparing the hash of the current file with
    the hash of the file when the builder was last
    executed. Dependencies files store those hashes between
    consecutive runs.

    A dependency file is attached to a builder, and has a name since
    one builder may have several dependencies files if dependencies
    come from different sources. Each file stores several (file, hash)
    assocations.
    """

    def __init__(self, builder, name):
        """Construct a dependency file for builder with given name."""
        self.__builder = builder
        self.name = name
        builder.targets().sort()
        self.__files = {}
        self.__sha1 = {}


    def files(self):
        """List of hashed files."""
        return self.__files.values()


    def sha1s(self):
        """Dictonary associating sha1s to files."""
        return self.__sha1


    def register(self, node):
        """Add the node to the hashed files."""
        self.__files[str(node.name())] = node


    def path(self):
        """Path to the file storing the hashes."""
        return self.__builder.cachedir() / self.name


    def read(self):
        """Read the hashes from the store file."""
        res = []
        self.path().touch()
        for line in open(str(self.path()), 'r'):
            chunks = line[:-1].split(' ')
            sha1 = chunks[0]
            name = ' '.join(chunks[1:-1])
            data = chunks[-1]
            src = Path(name)
            self.__sha1[str(src)] = (sha1, data)

    def up_to_date(self):
      """Whether all registered files match the stored hash."""
      for path in list(self.__sha1.keys()):
        if path not in Node.nodes:
          del self.__sha1[path]
          continue
        h = node(path).hash()
        if self.__sha1[path][0] != h:
          debug.debug(
              'Execution needed because hash is outdated: %s.' % path,
              debug.DEBUG_DEPS)
          return False
      return True


    def update(self):
      """Rehash all files and write to the store file."""
      with open(str(self.path()), 'w') as f:
        for path in self.__files:
          h = self.__files[path].hash()
          print('%s %s %s' % (h, self.__files[path].name(),
                              self.__files[path].drake_type()),
                file = f)

    def __repr__(self):
        """Python representation."""
        return 'DepFile(%s)' % repr(self.__builder)

    def __str__(self):
        """String representation."""
        return 'DepFile(%s)' % self.__builder

def path_build(path):
    """Return path as found in the build directory.

    This function prepend the necessary prefix to a path relative to
    the current drakefile to make it relative to the root of the build
    directory.

    When computing pathes in a drakefile, one might need to find the
    location of a path in the build directory relatively to this
    drakefile. Since the build is runned at the root of the build
    directory, if this drakefile is included from an other, path
    relative to this drakefile won't be valid.

    Most pathes are handled by nodes, so this function is rarely
    used. However it is sometimes necessary, when generating shell
    commands for instance.
    """
    path = Path(path)
    if path.absolute():
        return path
    return prefix() / path

def path_src(path):
    """Return path as found in the source directory.

    This function prepend the necessary prefix to a path relative to
    the current drakefile to make it relative to the root of the
    source directory.

    This function is similar to path_build, except for the source
    directory.
    """
    path = Path(path)
    if path.absolute():
        return path
    return srctree() / path_build(path)

def path_root():
    """The directory containing the root drakefile."""
    return Path(_OS.getcwd())

class _BaseNodeTypeType(type):

    node_types = {}

    def __call__(c, name, *arg, **kwargs):

        res = type.__call__(c, name, *arg, **kwargs)
        k = '%s.%s' % (res.__module__, res.__name__)
        _BaseNodeTypeType.node_types[k] = res
        return res

        return type.__call__(*arg)

class _BaseNodeType(type, metaclass = _BaseNodeTypeType):

    def __call__(c, *args, **kwargs):

        try:
            return type.__call__(c, *args, **kwargs)
        except NodeRedefinition as e:
            assert e.name() in BaseNode.nodes
            node = BaseNode.nodes[e.name()]
            assert node.__class__ is c
            return node


class BaseNode(object, metaclass = _BaseNodeType):

    """Base entity manipulated by drake.

    Nodes represent the base elements that can be built or used as
    input by the drake buildsystem. Builders have a list of sources
    nodes, which are the node they use as input when they are
    executed, and produce nodes as output.

    Nodes are often attached to file (an input file in the case of a
    source node, a generated file in the case of a target node), in
    which case its type is Node."""

    nodes = {}
    uid = 0
    extensions = {}

    def __init__(self, name):
        """Create a node with the given name."""
        if str(name) in BaseNode.nodes:
            raise NodeRedefinition(str(name))
        self.__name = name
        self.uid = BaseNode.uid
        BaseNode.uid += 1
        self.builder = None
        self.srctree = srctree()
        BaseNode.nodes[str(name)] = self
        self.consumers = []

    def name(self):
        """Node name, relative to the current drakefile."""
        res = Path(self.__name)
        res.strip_prefix(prefix())
        return res

    def name_absolute(self):
        """Node name, relative to the root of the source directory."""
        return self.__name

    def dot(self, marks):
        """Print a dot representation of this nodes build graph."""
        if (self in marks):
            return True
        marks[self] = None
        print('  node_%s [label="%s"]' % (self.uid, self.__name))
        if self.builder is not None:
            if self.builder.dot(marks):
                print('  builder_%s -> node_%s' % (self.builder.uid,
                                                   self.uid))
        return True

    @classmethod
    def drake_type(self):
        """The qualified name of this type."""
        return '%s.%s' % (self.__module__, self.__name__)

    def __str__(self):
        """String representation."""
        return str(self.name())

    def __repr__(self):
        """Python representation."""
        return '%s(%s)' % (self.__class__.drake_type(), self.name())

    def hash(self):
        """Hash for this node.

        The hash value of nodes is used by builders to determine
        whether a source node has changed between two builds, and thus
        if the builder must be reexecuted.

        It must be reimplemented by subclasses.
        """
        raise Exception('hash must be implemented by BaseNodes')

    def build(self):
        """Build this node.

        Take necessary action to ensure this node is up to date. That
        is, roughly, run this node runner.
        """
        if not _scheduled():
            Coroutine(self.build, str(self), _scheduler())
            _scheduler().run()
        else:
            debug.debug('Building %s.' % self, debug.DEBUG_TRACE)
            with debug.indentation():
                if self.builder is None:
                    self.polish()
                    return
                self.builder.run()
            self.polish()

    @property
    def build_status(self):
      return self.builder.build_status

    def polish(self):
        """A hook called when a node has been built.

        Called when a node has been built, that is, when all its
        dependencies have been built and the builder run. Default
        implementation does nothing.

        >>> class MyNode (Node):
        ...   def polish(self):
        ...     print('Polishing.')
        >>> n = MyNode('/tmp/.drake.polish')
        >>> n.path().remove()
        >>> b = TouchBuilder(n)
        >>> n.build()
        Touch /tmp/.drake.polish
        Polishing.
        """
        pass

    def clean(self):
        """Clean recursively for this node sources."""
        if self.builder is not None:
            self.builder.clean()

    def missing(self):
        """Whether this node is missing and must be built.

        Always False, so unless redefined, BaseNode are built only if
        a dependency changed.
        """
        return False

    def makefile_name(self):
        if isinstance(self, Node):
            return str(self.path())
        else:
            res = Path(self.name())
            res.virtual = False
            return str(res)

    def makefile(self, marks = None):
      """Print a Makefile for this node."""
      from pipes import quote
      if self.builder is None:
        return
      if marks is None:
        marks = set()
      if str(self.name()) in marks:
        return
      else:
        marks.add(str(self.name()))
      print('%s: %s' % (self.makefile_name(),
                        ' '.join(map(lambda n: n.makefile_name(),
                                     self.dependencies))))
      cmd = self.builder.command
      if cmd is not None:
        if isinstance(self, Node):
          print('\t@mkdir -p %s' % self.path().dirname())
        if not isinstance(cmd, tuple):
          cmd = (cmd,)
        for c in cmd:
          print('\t%s' % ' '.join(
              map(lambda a: quote(str(a)).replace('$', '$$'), c)))
      print('')
      for dependency in self.dependencies:
        dependency.makefile(marks)

    @property
    def dependencies(self):
        """All first-level dependencies"""
        return itertools.chain(self.builder.sources().values(),
                               self.builder._Builder__dynsrc.values())

    def report_dependencies(self, dependencies):
        """Called when dependencies have been built.

        This hook is always called no matter whether the nodes
        were successfully built or not.
        """
        pass

class VirtualNode(BaseNode):

    """BaseNode that does not represent a file.

    These may be configuration or meta information such as the version
    system revision, used by other nodes as sources. They are also
    used to implement Rule, which is a node that recursively builds
    other nodes, but does not directly produce a file.
    """

    def __init__(self, name):
        """Create a virtual node with the given name."""
        path = prefix() / name
        path.virtual = True
        BaseNode.__init__(self, path)

    def hash(self):
        """Virtual node children must reimplement BaseNode.hash."""
        raise Exception('hash must be implemented by VirtualNodes')


class Node(BaseNode):

    """BaseNode representing a file."""

    def __init__(self, path):
        """Construct a Node with the given path."""
        self.__hash = None
        path = Path(path)
        if not path.absolute():
            path = prefix() / path
        BaseNode.__init__(self, path)

    def clone(self, path):
        """Clone of this node, with an other path."""
        return Node(path)

    def hash(self):
        """Digest of the file as a string."""
        if self.__hash is None:
            with open(str(self.path()), 'rb') as f:
                hasher = hashlib.sha1()
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    hasher.update(chunk)
            self.__hash = hasher.hexdigest()
        return self.__hash

    def clean(self):
        """Clean this node's file if it is generated, and recursively
        its sources recursively."""
        BaseNode.clean(self)
        if self.builder is not None and self.path().exists():
            print('Deleting %s' % self)
            _OS.remove(str(self.path()))

    def path(self):
        """Filesystem path to node file, relative to the root of the
        build directory.

        >>> with Drake('source/tree') as drake:
        ...   n = node('file')
        ...   print(n.path())
        ...   builder = TouchBuilder([n])
        ...   print(n.path())
        ...   n = node('//virtual/node')
        ...   print(n.path())
        source/tree/file
        file
        //virtual/node
        """
        if self.name().absolute() or self.name().virtual:
            # assert self.builder is None
            return self.name()
        if self.builder is None:
            return self.srctree / self.name_absolute()
        else:
            return self.name_absolute()

    def missing(self):
        """Whether the associated file doesn't exist.

        Nodes are built if their file does not exist.
        """
        return not self.path().exists()

    def build(self):
        """Builds this node.

        Building a Node raises an error if the associated file does
        not exist and it has no builder.

        >>> n = node('/tmp/.drake.node')
        >>> n.path().remove()
        >>> n.build()
        Traceback (most recent call last):
            ...
        drake.NoBuilder: no builder to make /tmp/.drake.node

        If the file exist, drake consider it is a provided input and
        building it does nothing.

        >>> n.path().touch()
        >>> n.build()

        If a Node needs to be built and its builder is executed, it
        must create the Node's associated file.

        >>> n.path().remove()
        >>> class EmptyBuilder(Builder):
        ...   def execute(self):
        ...     return True
        >>> builder = EmptyBuilder([], [n])
        >>> n.build()
        Traceback (most recent call last):
            ...
        drake.Exception: /tmp/.drake.node wasn't created by EmptyBuilder
        """
        if not _scheduled():
            Coroutine(self.build, str(self), _scheduler())
            _scheduler().run()
        else:
            debug.debug('Building %s.' % self, debug.DEBUG_TRACE)
            with debug.indentation():
                if self.builder is None:
                    if self.missing():
                        raise NoBuilder(self)
                    self.polish()
                    return
                self.builder.run()
            self.polish()

    def __setattr__(self, name, value):
        """Adapt the node path is the builder is changed."""
        if name == 'builder' and 'builder' in self.__dict__:
            del self.nodes[self._BaseNode__name]
            self.__dict__[name] = value
            self.nodes[self._BaseNode__name] = self
        else:
            self.__dict__[name] = value

    def __repr__(self):
        """Filesystem path to the node file, as a string."""
        return str(self.path())

    def __lt__(self, rhs):
        """Arbitrary global order on nodes, to enable
        sorting/indexing."""
        return self.path() < rhs.path()

    @property
    def install_command(self):
      return None


def node(path, type = None):
    """Create or get a BaseNode.

    path -- path to the node file.
    type -- optional type of the node.

    The returned node is determined as follow:
    * If a node exists for the given path, it is returned.
    * If the type is given, a node of that type is constructed with
      the path as argument.
    * If the path as a known extension, a node of the associated type
      is constructed with the path as argument.
    * A simple Node with that path is constructed.
    """
    if path.__class__ != Path:
        path = Path(path)
    if str(path) in Node.nodes:
        return Node.nodes[str(path)]
    if type is not None:
        return type(path)
    if path.extension not in Node.extensions:
        return Node(path)
    return Node.extensions[path.extension](path)


def nodes(*paths):
    """Call node() on each given path and return the list of results.

    nodes('foo', 'bar', ...) is equivalent to
    [node('foo'), node('bar'), ...]
    """
    return list(map(node, paths))


def _cmd_escape(fmt, *args):
    rg = re.compile('\'')
    args = list(map(str, args))
    for arg in args:
        if rg.match(arg):
            pass
    return fmt % tuple(args)


def cmd(cmd, cwd = None, stdout = None):
    """Run the shell command.

    cmd -- the shell command.
    cwd -- the dir to chdir to before.
    """
    if cwd is not None:
        cwd = str(cwd)
    p = subprocess.Popen(cmd,
                         shell = True,
                         cwd = cwd,
                         stdout = stdout)
    p.wait()
    return p.returncode == 0

def command(cmd, cwd = None, stdout = None):
    """Run the shell command.

    cmd -- the shell command.
    cwd -- the dir to chdir to before.
    """
    if cwd is not None:
        cwd = str(cwd)
    p = subprocess.Popen(cmd, cwd = cwd, stdout = stdout)
    p.wait()
    return p.returncode == 0


def cmd_output(cmd, cwd = None):
    """Run the given command and return its standard output.

    Expansion handles shell escaping.
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, cwd = cwd)
    res = p.communicate()[0]
    if p.returncode != 0:
        raise Exception('command failed: %s' % cmd)
    return res

def _can_skip_node(node):
    if node.builder is None:
        if isinstance(node, Node):
            return not node.missing()
        else:
            return True
    else:
        return node.builder._Builder__executed

class Builder:

    """Produces a set of BaseNodes from an other set of BaseNodes."""

    builders = []
    uid = 0

    name = 'build'
    _deps_handlers = {}

    class Failed(Exception):

      def __init__(self, builder):
        self.__builder = builder

      def __str__(self):
          return '%s failed' % self.__builder

    @classmethod
    def register_deps_handler(self, name, f):
        """Add a dependency handler."""
        self._deps_handlers[name] = f

    def __init__(self, srcs, dsts):
        """Create a builder.

        srcs -- List of source nodes, or source node if there is
                only one.
        dsts -- List of target nodes, or target node if there is
                only one.
        """
        self.__sources = {}
        self.__vsrcs = {}
        for src in srcs:
            self.add_src(src)
#        self.__sources = srcs
        self.__targets = dsts
        for dst in dsts:
            if dst.builder is not None:
                raise Exception('builder redefinition for %s' % dst)
            dst.builder = self

        self.uid = Builder.uid
        Builder.uid += 1
        Builder.builders.append(self)

        self._depfiles = {}
        self._depfile = DepFile(self, 'drake')
        self.__depfile_builder = DepFile(self, 'drake.Builder')
        self.__executed = False
        self.__executed_exception = None
        self.__executed_signal = None
        self.__dynsrc = {}

    def sources_dynamic(self):
        """The list of dynamic source nodes."""
        return self.__dynsrc.values()

    def sources(self):
        """The list of source nodes."""
        return self.__sources

    def targets(self):
        """The list of target nodes."""
        return self.__targets

    @property
    def path_stdout(self):
      return self.cachedir() / 'stdout'

    def cmd(self, pretty, cmd, cwd = None, leave_stdout = False):
        """Run a shell command.

        pretty  -- A pretty version for output.
        command -- The command.

        The expansion handles shell escaping. The pretty version is
        printed, except if drake is in raw mode, in which case the
        actual command is printed.
        """
        if not isinstance(cmd, tuple):
          cmd = (cmd,)
        with open(str(self.path_stdout), 'w') as f:
            def fun():
                results = []
                for c in cmd:
                    c = list(map(str, c))
                    if pretty is not None:
                      self.output(' '.join(c), pretty)
                    stdout = None
                    if not leave_stdout:
                        stdout = f
                    results.append(command(c, cwd = cwd, stdout = stdout))
                return all(results)
            if _JOBS_LOCK is not None:
                with _JOBS_LOCK:
                    return sched.background(fun)
            else:
                return fun()

    def output(self, raw, pretty = None):
        """Output pretty, or raw if drake is in raw mode."""
        if not _SILENT:
            print((not _RAW and pretty) or raw)


    def cachedir(self):
        """The cachedir that stores dependency files."""
        path = self.__targets[0].name()
        res = path.dirname() / _CACHEDIR / path.basename()
        if not res.absolute():
            res = prefix() / res
        res.mkpath()
        return res


    def hash(self):
        """A hash for this builder"""
        return None

    def dependencies(self):
        """Recompute dynamic dependencies list and return them.

        Reimplemented by subclasses. This implementation returns an
        empty list.
        """
        pass


    def depfile(self, name):
        """The depfile for this node for static dependencies."""
        if name not in self._depfiles:
            self._depfiles[name] = DepFile(self, name)
        return self._depfiles[name]


    def add_dynsrc(self, name, node, data = None):
        """Add a dynamic source node."""
        self.depfile(name).register(node)
        self.__dynsrc[str(node.path())] = node


    def get_type(self, tname):
        """Return the node type with the given name."""
        return _BaseNodeTypeType.node_types[tname]

    def report_dependencies(self, dependencies):
        """Called when dependencies have been built.

        This hook is always called no matter whether the nodes
        were successfully built or not.
        """
        pass

    def __report_dependencies(self, dependencies):
        self.report_dependencies(dependencies)
        for target in self.__targets:
            target.report_dependencies(dependencies)

    @property
    def build_status(self):
      if not self.__executed:
        return None
      else:
        return self.__executed_exception is None

    def run(self):
        """Build sources recursively, check if our target are up to
        date, and executed if needed."""

        debug.debug('Running %s.' % self, debug.DEBUG_TRACE_PLUS)
        with debug.indentation():

            if not self.__executed:
                # If someone is already executing this builder, wait.
                if self.__executed_signal is not None:
                    debug.debug('Already being built, waiting.',
                                debug.DEBUG_TRACE_PLUS)
                    sched.coro_wait(self.__executed_signal)
                # Otherwise, build it ourselves
                else:
                    self.__executed_signal = sched.Signal()

            # If we were already executed, just skip
            if self.__executed:
              if self.__executed_exception is not None:
                debug.debug(
                    'Already built in this run, with an exception.',
                    debug.DEBUG_TRACE_PLUS)
                raise self.__executed_exception
              debug.debug('Already built in this run.',
                          debug.DEBUG_TRACE_PLUS)
              return
            try:
                # The list of static dependencies is now fixed
                for path in self.__sources:
                    self._depfile.register(self.__sources[path])

                # See Whether we need to execute or not
                execute = False

                # Reload dynamic dependencies
                if not execute:
                  for f in _OS.listdir(str(self.cachedir())):
                    if f in ['drake', 'drake.Builder', 'stdout']:
                      continue
                    debug.debug(
                      'Considering dependencies file %s' % f,
                      debug.DEBUG_DEPS)
                    depfile = self.depfile(f)
                    depfile.read()
                    handler = self._deps_handlers[f]
                    with debug.indentation():
                      for path in depfile.sha1s():
                        if path in self.__sources or path in self.__dynsrc:
                          debug.debug(
                              '%s is already in our sources.' % path,
                              debug.DEBUG_DEPS)
                          continue
                        if path in Node.nodes:
                          node = Node.nodes[path]
                        else:
                          debug.debug('%s is unknown, calling handler.' % path,
                                      debug.DEBUG_DEPS)
                          node = handler(self,
                                         path,
                                         self.get_type(depfile.sha1s()[path][1]),
                                         None)
                        debug.debug('Adding %s to our sources.' % node, debug.DEBUG_DEPS)
                        self.add_dynsrc(f, node, None)


                coroutines_static = []
                coroutines_dynamic = []

                # FIXME: symetric of can_skip_node: if a node is a
                # plain file and does not exist, err immediately (or
                # execute = True).

                # Build static dependencies
                debug.debug('Build static dependencies')
                with debug.indentation():
                    for node in list(self.__sources.values()) + \
                        list(self.__vsrcs.values()):
                        if _can_skip_node(node):
                            continue
                        coroutines_static.append(
                            Coroutine(node.build,
                                      str(node),
                                      _scheduler(),
                                      sched.Coroutine.current))
                try:
                  sched.coro_wait(coroutines_static)
                finally:
                  self.__report_dependencies(self.__sources.values())

                # Build dynamic dependencies
                debug.debug('Build dynamic dependencies')
                with debug.indentation():
                    for path in self.__dynsrc:
                        node = self.__dynsrc[path]
                        if _can_skip_node(node):
                            continue
                        coroutines_dynamic.append(
                            Coroutine(node.build,
                                      str(node),
                                      _scheduler(),
                                      sched.Coroutine.current))

                try:
                    sched.coro_wait(coroutines_dynamic)
                except Exception as e:
                    debug.debug('Execution needed because some '
                                'dynamic dependency couldn\'t '
                                'be built')
                    execute = True

                # If any non-virtual target is missing, we must rebuild.
                if not execute:
                    for dst in self.__targets:
                        if dst.missing():
                            debug.debug('Execution needed because '
                                        'of missing target: %s.' % \
                                        dst.path(),
                                        debug.DEBUG_DEPS)
                            execute = True

                # Load static dependencies
                self._depfile.read()

                # If a new dependency appeared, we must rebuild.
                if not execute:
                  for p in self.__sources:
                    path = self.__sources[p].name()
                    if str(path) not in self._depfile.sha1s():
                      debug.debug('Execution needed because a new '
                                  'dependency appeared: %s.' % path,
                                  debug.DEBUG_DEPS)
                      execute = True
                      break

                # Check if we are up to date wrt to the builder itself
                self.__builder_hash = self.hash()
                depfile_builder = self.cachedir() / _DEPFILE_BUILDER
                if not execute:
                  if self.__builder_hash is not None:
                    if depfile_builder.exists():
                      with open(str(depfile_builder), 'r') as f:
                        if self.__builder_hash != f.read():
                           debug.debug('Execution needed because the '
                                       'hash for the builder is '
                                       'outdated.',
                                       debug.DEBUG_DEPS)
                           execute = True
                    else:
                      debug.debug('Execution needed because the hash '
                                  'for the builder is unkown.',
                                  debug.DEBUG_DEPS)
                      execute = True

                # Check if we are up to date wrt all dependencies
                if not execute:
                    if not self._depfile.up_to_date():
                        execute = True
                    for f in self._depfiles:
                        if not self._depfiles[f].up_to_date():
                            execute = True


                if execute:
                    debug.debug('Executing builder %s' % self,
                                debug.DEBUG_TRACE)

                    # Regenerate dynamic dependencies
                    self.__dynsrc = {}
                    self._depfiles = {}
                    debug.debug('Recomputing dependencies',
                                debug.DEBUG_TRACE_PLUS)
                    with debug.indentation():
                        self.dependencies()

                    debug.debug('Rebuilding new dynamic dependencies',
                                debug.DEBUG_TRACE_PLUS)
                    with debug.indentation():
                        for node in self.__dynsrc.values():
                            # FIXME: parallelize
                            node.build()

                    if not self.execute():
                      self.__executed = True
                      self.__executed_exception = \
                        Builder.Failed(self)
                      raise self.__executed_exception

                    # Check every non-virtual target was built.
                    for dst in self.__targets:
                      if isinstance(dst, Node):
                        if dst.missing():
                          raise Exception('%s wasn\'t created by %s' \
                                          % (dst, self))
                        dst._Node__hash = None

                    # Update depfiles
                    self._depfile.update()
                    debug.debug('Write dependencies file %s' % \
                                self._depfile,
                                debug.DEBUG_TRACE_PLUS)
                    if self.__builder_hash is None:
                      debug.debug('Remove builder dependency file %s'\
                                  % depfile_builder,
                                  debug.DEBUG_TRACE_PLUS)
                      depfile_builder.remove()
                    else:
                      debug.debug('Write builder dependency file %s'\
                                  % depfile_builder,
                                  debug.DEBUG_TRACE_PLUS)
                      with open(str(depfile_builder), 'w') as f:
                        print(self.__builder_hash, file = f, end = '')
                    # FIXME: BUG: remove dynamic dependencies files
                    # that are no longer present, otherwise this will
                    # be rebuilt forever.
                    for name in self._depfiles:
                      debug.debug('Write dependencies file %s' % name,
                                  debug.DEBUG_TRACE_PLUS)
                      self._depfiles[name].update()
                    self.__executed = True
                else:
                    self.__executed = True
                    debug.debug('Everything is up to date.',
                                debug.DEBUG_TRACE_PLUS)
            except Exception as e:
              self.__executed_exception = e
              raise
            finally:
              self.__executed = True
              self.__executed_signal.signal()


    def execute(self):
        """Generate target nodes from source node.

        Must be reimplemented by subclasses.
        """
        raise Exception('execute is not implemented for %s' % self)


    def clean(self):
        """Clean source nodes recursively."""
        for node in list(self.__sources.values()) + \
            list(self.__vsrcs.values()):
            node.clean()


    def __str__(self):
        """String representation."""
        return self.__class__.__name__


    def add_src(self, src):
        """Add a static source."""
        self.__sources[str(src.name_absolute())] = src
        src.consumers.append(self)


    def add_virtual_src(self, src):
        """Add a virtual source.

        Virtual sources are built when the builder is runned, but are
        not taken in account when determining if this builder must be
        executed.
        """
        self.__vsrcs[str(src.path())] = src


    def all_srcs(self):
        """All sources, recursively."""
        res = []
        for src in self.__sources.values() + self.__dynsrc.values():
            res.append(src)
            if src.builder is not None:
                res += src.builder.all_srcs()
        return res

    def dot(self, marks):
        """Print a dot representation of the build graph."""
        if (self in marks):
            return True
        marks[self] = None

        print('  builder_%s [label="%s", shape=rect]' % \
              (self.uid, self.__class__))
        for node in itertools.chain(self.__sources.values(),
                                    self.__dynsrc.values()):
            if node.dot(marks):
                print('  node_%s -> builder_%s' % (node.uid, self.uid))
        return True


class ShellCommand(Builder):

    """A builder that runs a shell command.

    This builder is a commodity to create a builder that simply runs a
    shell commands, or to subclass so you don't need to reimplement
    execute.

    >>> path = Path("/tmp/.drake.foo")
    >>> n = node("/tmp/.drake.foo")
    >>> b = ShellCommand([], [n], ['touch', '/tmp/.drake.foo'])
    >>> path.remove()
    >>> n.build()
    touch /tmp/.drake.foo
    >>> path.exists()
    True
    """

    def __init__(self, sources, targets, command, pretty = None):
        """Create a builder that runs command.

        sources -- List of source nodes, or source source node if
                   there's only one.
        targets -- List of target nodes, or target target node if
                   there's only one.
        command -- The shell command to run.
        pretty  -- Optional pretty printing.
        """
        Builder.__init__(self, sources, targets)
        self.__command = command
        self.__pretty = pretty

    def execute(self):
        """Run the command given at construction time."""
        return self.cmd(self.__pretty or ' '.join(self.__command),
                        self.__command)

    @property
    def command(self):
        return self.__command

class Dictionary(VirtualNode):

    """A virtual node that represents a dictionary.

    This kind of node is useful to represent a set of key/value
    association that can be used as in input source for a builder,
    such as configuration.
    """

    def __init__(self, name, content = {}):
        """Build a dictionary with given content.

        name    -- The node name.
        content -- The content, as a dictionary.
        """
        VirtualNode.__init__(self, '%s' % name)
        self.content = content

    def hash(self):
        """Hash value."""
        # FIXME: sha1 of the string repr ain't optimal
        items = list(self)
        items.sort()
        return hashlib.sha1(str(items).encode('utf-8')).hexdigest()

    def __iter__(self):
        """Iterate over the (key, value) pairs."""
        return iter(self.content.items())


class Expander(Builder):

    """A builder that expands content of Dictionary in text.

    This class becomes useful when subclass define the content()
    method, that returns text in which to expand values. See
    FileExpander, TextExpander and FunctionExpander.

    >>> class MyExpander(Expander):
    ...     def __init__(self, content, *args, **kwargs):
    ...         Expander.__init__(self, *args, **kwargs)
    ...         self.__content = content
    ...     def content(self):
    ...         return self.__content
    >>> colors  = Dictionary('colors',  { 'apple-color':  'red',
    ...                                   'banana-color':  'yellow' })
    >>> lengths = Dictionary('lengths', { 'apple-length': 10,
    ...                                   'banana-length': 15 })
    >>> target = Node('/tmp/.drake.expander.1')
    >>> builder = MyExpander(
    ...   'Apples are @apple-color@, bananas are @banana-color@.',
    ...   [colors, lengths], target)
    >>> target.path().remove()
    >>> target.build()
    Expand /tmp/.drake.expander.1
    >>> open('/tmp/.drake.expander.1').read()
    'Apples are red, bananas are yellow.\\n'

    The expanded pattern can me configured by setting a custom
    matcher. The matcher must be a regexp that contains at least one
    group, it is searched in the content, then the match of the first
    group is used as a key to search source dictionaries, and the
    whole match is replaced with the obtained value. For instance, the
    default '@([a-zA-Z0-9_-]+)@' will match autoconf-style variables,
    '@name@'. Here is an example with shell-style variables $name
    (except dashes are accepted):

    >>> target = Node('/tmp/.drake.expander.2')
    >>> builder = MyExpander('Bananas are $banana-length '
    ...                      'centimeters long.',
    ...                      [colors, lengths], target,
    ...                      matcher = '\\$([a-zA-Z0-9][-_a-zA-Z0-9]*)')
    >>> target.path().remove()
    >>> target.build()
    Expand /tmp/.drake.expander.2
    >>> open('/tmp/.drake.expander.2').read()
    'Bananas are 15 centimeters long.\\n'

    The behavior in case a key is not found can be adjusted with
    missing_fatal:

    >>> target = Node('/tmp/.drake.expander.3')
    >>> builder = MyExpander('Kiwis are @kiwi-color@.',
    ...                      [colors, lengths], target)
    >>> print(builder.missing_fatal())
    True
    >>> target.path().remove()
    >>> target.build()
    Traceback (most recent call last):
      ...
    drake.Failed: MyExpander failed
    >>> target.builder = None
    >>> builder = MyExpander('Kiwis are @kiwi-color@.',
    ...                      [colors, lengths], target,
    ...                      missing_fatal = False)
    >>> builder.missing_fatal()
    False
    >>> target.build()
    Expand /tmp/.drake.expander.3
    >>> open('/tmp/.drake.expander.3').read()
    'Kiwis are @kiwi-color@.\\n'
    """

    def __init__(self, dicts, target, sources = [],
               matcher = '@([a-zA-Z0-9_-]+)@', missing_fatal = True):
      """Create and expander that expands the given dictionaries.

      dicts         -- The dictionaries from which to expand keys.
      sources       -- List of additional source nodes,
                       or additional source node if there's only one.
      target        -- The target Node where to store the result.
      matcher       -- A regexp to find the patterns to expand in the
                       content.
      missing_fatal -- Whether a key in the content missing from the
                       dictionaries is fatal.
      """
      if not isinstance(dicts, list):
          dicts = [dicts]

      Builder.__init__(self, sources + dicts, [target])
      self.__dicts = dicts
      self.matcher = re.compile(matcher)
      self.__missing_fatal = missing_fatal
      self.__target = target

    def missing_fatal(self):
        return self.__missing_fatal

    def execute(self):
        """Expand the keys in the content and write to target file."""
        self.output('Expand %s' % (self.__target))
        vars = {}
        for d in self.__dicts:
            vars.update(dict(d))
        content = self.content()
        for match in self.matcher.finditer(content):
            key = match.group(1)
            try:
                content = content.replace(match.group(0),
                                          str(vars[key]))
            except KeyError:
                if self.__missing_fatal:
                    print('Missing expansion: %s' % key)
                    return False

        with open(str(self.__target.path()), 'w') as f:
            print(content, file = f)
        return True

    def dictionaries(self):
        """The list of source dictionary."""
        return self.__dicts

    def target(self):
        """The target Node."""
        return self.__target

class FileExpander(Expander):
    """An Expander that takes its content from a file.

    >>> source = Node('/tmp/.drake.file.expander.source')
    >>> with open(str(source.path()), 'w') as f:
    ...   print('Expand @this@.', file = f)
    >>> target = Node('/tmp/.drake.file.expander.target')
    >>> builder = FileExpander(source, [Dictionary('d_file',
    ...                                 { 'this': 'that' })], target)
    >>> target.path().remove()
    >>> target.build()
    Expand /tmp/.drake.file.expander.target
    >>> open('/tmp/.drake.file.expander.target').read()
    'Expand that.\\n\\n'
    """
    def __init__(self, source, dicts, target = None, *args, **kwargs):
      """Create a file expander.

      source       -- The file to expand.
      args, kwargs -- Rest of the arguments for Expander constructor.
      """
      self.__source = source
      assert isinstance(source, BaseNode)
      if target is None:
        target = Path(source.name())
        target.extension_strip_last_component()
        target = node(target)
      else:
        assert isinstance(target, BaseNode)
        self.__target = target
      Expander.__init__(self,
                        dicts = dicts,
                        sources = [source],
                        target = target,
                        *args, **kwargs)

    def execute(self):
        if Expander.execute(self):
            shutil.copymode(str(self.__source.path()),
                            str(self.__target.path()))
            return True
        else:
            return False

    def content(self):
        """The content of the source file."""
        return open(str(self.__source.path()), 'r').read()

    def source(self):
        """The source node."""
        return self.__source


class TextExpander(Expander):
  """An Expander with a static content.

  >>> target = Node('/tmp/.drake.text.expander')
  >>> builder = TextExpander('Expand @this@.',
  ...                        [Dictionary('d_text',
  ...                                    { 'this': 'that' })], target)
  >>> target.path().remove()
  >>> target.build()
  Expand /tmp/.drake.text.expander
  >>> open('/tmp/.drake.text.expander').read()
  'Expand that.\\n'
  """
  def __init__(self, text, *args, **kwargs):
      """Create a text expander.

      text         -- The text to expand.
      args, kwargs -- Rest of the arguments for Expander constructor.
      """
      self.__text = text
      Expander.__init__(self, *args, **kwargs)

  def content(self):
      """The text."""
      return self.__text;

  def text(self):
      """The text."""
      return self.__text

class FunctionExpander(Expander):

    """An Expander that maps a function on the dictionaries content.

    >>> target = Node('/tmp/.drake.function.expander')
    >>> version = Dictionary('version', { 'version_major': 4,
    ...                                   'version_minor': 2 })
    >>> def define(k, v):
    ...     return '# define %s %s\\n' % (k.upper(), v)
    >>> builder = FunctionExpander(define, [version], target)
    >>> target.path().remove()
    >>> target.build()
    Expand /tmp/.drake.function.expander
    >>> open('/tmp/.drake.function.expander').read()
    '# define VERSION_MINOR 2\\n# define VERSION_MAJOR 4\\n\\n'
    """

    def __init__(self, function, *args, **kwargs):
      """Create a function expander.=

      function     -- The function to apply on key, values pairs.
      args, kwargs -- Rest of the arguments for Expander constructor.
      """
      self.__function = function
      Expander.__init__(self, *args, **kwargs)

    def content(self):
        """The content obtained by mapping the function on the
        dictionaries."""
        res = ''
        for d in self.dictionaries():
            for key, value in d:
                res += self.__function(key, value)
        return res

    def function(self):
        """The function."""
        return self.__function

_prefix = Path('')

def prefix():
    """The current prefix.

    The prefix is the path from the root of the build tree to the
    current drakefile build tree. This is '.' for the root drakefile.
    """
    return _prefix

_srctree = Path('')

def srctree():
    """Path to the root of the source tree, from the root of the
    build tree."""
    global _srctree
    return _srctree

class _Module:

    def __init__(self, globals):
        self.globals = globals

    def __getattr__(self, name):
        return self.globals[name]


def include(path, *args, **kwargs):
    """Include a sub-drakefile.

    path         -- Path to the directory where the drakefile is
                    located.
    args, kwargs -- Arguments for the drakefile's configure.

    Load the drakefile found in the specified directory, merge its
    graph with ours and return an object that has all variables
    defined globally by the sub-drakefile as attributes.
    """
    global _prefix
    path = Path(path)
    previous_prefix = _prefix
    _prefix = _prefix / path
    drakefile = None
    names = ['drakefile', 'drakefile.py']
    for name in names:
        if path_src(name).exists():
            drakefile = previous_prefix / path / name
            break
    if drakefile is None:
        raise Exception('cannot find %s or %s in %s' % \
                        (', '.join(names[:-1]), names[-1], path))
    res = _raw_include(str(drakefile), *args, **kwargs)
    _prefix = previous_prefix
    return res


def _raw_include(path, *args, **kwargs):

    g = {}
    path = str(srctree() / path)
    #execfile(path, g)
    exec(compile(open(path).read(), path, 'exec'), g)
    res = _Module(g)
    res.configure(*args, **kwargs)
    return res

def dot(node, *filters):

    # FIXME: coro!
    node.build()
    marks = {}
    print('digraph')
    print('{')
    node.dot(marks)
    print('}')

_MODES = {}

def command_add(name, action):
    """Register a new command available from the command line.

    name   -- The name of the command.
    action -- The function called by the command.

    Using --name node_list on the command line will call action with
    the node list as argument.
    """
    _MODES[name] = action

def _register_commands():

    class CWDPrinter:
        def __enter__(self):
            print('%s: Entering directory `%s\'' % (sys.argv[0],
                                                    _OS.getcwd()))
        def __exit__(self, *args):
            print('%s: Leaving directory `%s\'' % (sys.argv[0],
                                                   _OS.getcwd()))

    def all_if_none(nodes):
        # Copy it, since it will change during iteration. This shouldn't
        # be a problem, all newly inserted will be dependencies of the
        # already existing nodes. Right?
        if len(nodes):
            return nodes
        else:
            return list(Node.nodes.values())

    def build(nodes):
      with CWDPrinter():
        try:
          if not len(nodes):
            nodes = [node for node in Node.nodes.values()
                     if not len(node.consumers)]
          coroutines = []
          for node in nodes:
            coroutines.append(Coroutine(node.build, str(node),
                                        _scheduler()))
          _scheduler().run()
        except Builder.Failed as e:
          print('%s: *** %s' % (sys.argv[0], e))
          exit(1)
    command_add('build', build)

    def clean(nodes):
        with CWDPrinter():
            for node in all_if_none(nodes):
                node.clean()
    command_add('clean', clean)

    def dot_cmd(nodes):
        for node in all_if_none(nodes):
            dot(node)
    command_add('dot', dot_cmd)

    def dot_show_cmd(nodes):
        if not len(nodes):
            print('%s: dot-show: give me some nodes to show.' % \
                  sys.argv[0])
        for node in nodes:
            p = subprocess.Popen('dot -Tpng | xv -',
                                 shell = True,
                                 stdin = subprocess.PIPE)
            stdout = sys.stdout
            sys.stdout = p.stdin
            dot(node)
            p.communicate()
            sys.stdout = stdout
    command_add('dot-show', dot_show_cmd)

    def makefile(nodes):
        root_nodes = [node for node in Node.nodes.values()
                      if not len(node.consumers)]
        if not len(nodes):
            nodes = root_nodes
        print('all: %s\n' % ' '.join(map(lambda n: n.makefile_name(),
                                         root_nodes)))
        marks = set()
        for node in nodes:
            node.makefile(marks)
    command_add('makefile', makefile)

_register_commands()


def _scheduler():
    res = Scheduler.scheduler()
    if res is None:
        return Scheduler()
    else:
        return res

_JOBS_LOCK = None
def _jobs_set(n):
    global _JOBS_LOCK
    n = int(n)
    if n == 1:
        _JOBS_LOCK = None
    else:
        _JOBS_LOCK = sched.Semaphore(n)

_ARG_DOC_RE = re.compile('\\s*(\\w+)\\s*--\\s*(.*)')
def _args_doc(doc):
    res = {}
    for line in doc.split('\n'):
        match = _ARG_DOC_RE.match(line)
        if match:
            res[match.group(1)] = match.group(2)
    return res


def help():
  print('%s [OPTIONS] [CONFIG] [ACTIONS]' % sys.argv[0])
  print('''\

OPTIONS:
\t--help, -h: print this usage and exit.
\t--jobs N, -j N: set number of concurrent jobs to N.
''')

  print('CONFIG:')
  doc = {}
  if _CONFIG.__doc__ is not None:
    doc = _args_doc(_CONFIG.__doc__)
  specs = inspect.getfullargspec(_CONFIG)
  for arg in specs.args:
    type = str
    if arg in specs.annotations:
      type = specs.annotations[arg]
    if type is str:
      type = 'string'
    elif type is bool:
      type = 'boolean'
    sys.stdout.write('\t--%s=%s' % (arg, type))
    if arg in doc:
      print(': %s' % doc[arg])
    else:
      print()
  print('''\

ACTIONS:
	--build [NODES]: build NODES, or all nodes in NODES is empty.
	--clean [NODES]: recursively delete all generated ancestors of
	  NODES, or all generated nodes in NODES is empty.
	--dot NODES: generate a dot dependency graph on stdout for
	  NODES (requires dot).
	--dot-show NODES: show a dependency graph for NODES (requires
	  dot and xv).''')
  exit(0)

_OPTIONS = {
    '--jobs': _jobs_set,
    '-j'    : _jobs_set,
    '--help': help,
    '-h'    : help,
}

_ARG_CONF_RE = re.compile('--(\\w+)=(.*)')
_CONFIG = None

_DEFAULTS = []

def add_default_node(node):
  _DEFAULTS.append(node)

def run(root, *cfg, **kwcfg):
  try:
    drake(root, *cfg, **kwcfg)
  except Exception as e:
    print('%s: %s' % (sys.argv[0], e))
    if 'DRAKE_DEBUG_BACKTRACE' in _OS.environ:
      import traceback
      traceback.print_exc()
    exit(1)
  except KeyboardInterrupt:
    print('%s: interrupted.' % sys.argv[0])
    exit(1)


class Drake:

  def __init__(self, root):
    self.__root = Path(root)

  def __enter__(self):
    global _srctree
    _srctree = self.__root

  def __exit__(self, *args, **kwargs):
    global _srctree
    _srctree = Path('')


def drake(root, *cfg, **kwcfg):
  """Run a drakefile.

  root       -- The directory where the drakefile is located.
  cfg, kwcfg -- Arguments for the drakeile's configure.

  Load the drakefile located in root, configure it with the given
  arguments and run all action specified on the command line
  (sys.argv).
  """
  global _CONFIG, _srctree
  with Drake(root) as drake:
    args = sys.argv[1:]
    # Load the root drakefile
    g = {}
    path = str(srctree() / 'drakefile')
    # execfile(path, g)
    exec(compile(open(path).read(), path, 'exec'), g)
    root = _Module(g)
    _CONFIG = root.configure
    # Fetch configuration from the command line.
    i = 0
    specs = inspect.getfullargspec(root.configure)
    while i < len(args):
      match = _ARG_CONF_RE.match(args[i])
      if match:
        name = match.group(1)
        value = match.group(2)
        if name in specs.args:
          if name in specs.annotations:
            t = specs.annotations[name]
            if t is bool:
              if value.lower() in ['true', 'yes']:
                value = True
              elif value.lower() in ['false', 'no']:
                value = False
              else:
                raise Exception('invalid value for '
                                'boolean option %s: %s' % (name, value))
          kwcfg[name] = value
          del args[i]
          continue
      elif args[i] in _OPTIONS:
        opt = args[i]
        del args[i]
        opt_args = []
        for a in inspect.getfullargspec(_OPTIONS[opt]).args:
          opt_args.append(args[i])
          del args[i]
        _OPTIONS[opt](*opt_args)
        continue
      i += 1
    root.configure(*cfg, **kwcfg)
    mode = _MODES['build']
    i = 0
    while True:
      if i < len(args):
        arg = args[i]
        if arg[0:2] == '--':
          arg = arg[2:]
          if arg in _MODES:
            mode = _MODES[arg]
          else:
            raise Exception('Unknown option: %s.' % arg)
          i += 1
      nodes = []
      while i < len(args) and args[i][0:2] != '--':
        nodes.append(node(args[i]))
        i += 1
      if not nodes:
        nodes = _DEFAULTS
      mode(nodes)
      if i == len(args):
        break


class WritePermissions:

  def __init__(self, node):
    self.__path = str(node.path())

  def __enter__(self):
    # FIXME: errors!
    try:
      _OS.chmod(self.__path,
                _OS.stat(self.__path).st_mode | stat.S_IWUSR)
    except OSError as e:
      if e.errno == 2:
        pass
      else:
        raise

  def __exit__(self, *args):
    _OS.chmod(self.__path,
              _OS.stat(self.__path).st_mode & ~stat.S_IWRITE)


class Copy(Builder):

    """Builder to copy files.

    See the convenience function copy to copy multiple files easily.

    >>> source = node('/tmp/.drake.Copy.source')
    >>> with open(str(source.path()), 'w') as f:
    ...   print('Content.', file = f)
    >>> builder = Copy(source, '/tmp/.drake.Copy.dest')
    >>> target = builder.target()
    >>> target
    /tmp/.drake.Copy.dest
    >>> target.path().remove()
    >>> builder.target().build()
    Copy /tmp/.drake.Copy.dest
    >>> open(str(target.path()), 'r').read()
    'Content.\\n'
    """

    def __init__(self, source, to):
        """Create a copy builder.

        source -- Node to copy.
        to     -- Destination path.
        """
        self.__source = source
        self.__target = source.clone(Path(to))
        self.__target.builder = None
        Builder.__init__(self, [self.__source], [self.__target])

    @property
    def source(self):
      """The source node."""
      return self.__source

    def target(self):
        """The target node."""
        return self.__target

    def execute(self):
        """Copy the source to the target."""
        self.output('Copy %s to %s' % (self.__source.path(),
                                       self.__target.path()),
                    'Copy %s' % self.__target)
        return self._execute()

    def _execute(self):
      with WritePermissions(self.__target):
        shutil.copy2(str(self.__source.path()),
                     str(self.__target.path()))
      return True

    @property
    def command(self):
        return ['cp', self.__source.path(), self.__target.path()]


class Install(Copy):
  """Builder to install files.

  Same as copy, but also executes the node install hook.
  """

  def execute(self):
    self.output('Install %s to %s' % (self.source.path(),
                                   self.target().path()),
                'Install %s' % self.target())
    if not self._execute():
      return False
    if self.target().install_command is not None:
      with WritePermissions(self.target()):
        return self.cmd(pretty = None,
                        cmd = self.target().install_command)
    return True

  @property
  def command(self):
    cmd = super().command
    install_cmd = self.source.install_command
    if install_cmd is not None:
      return (cmd, install_cmd)
    else:
      return cmd


def __copy(sources, to, strip_prefix, builder):
  if isinstance(sources, list):
    res = []
    for node in sources:
        res.append(__copy(node, to, strip_prefix, builder))
    return res
  else:
    path = sources.name()
    if strip_prefix is not None:
      path.strip_prefix(strip_prefix)
    path = Path(to) / path
    return builder(sources, path).target()


def copy(sources, to, strip_prefix = None):
  """Convenience function to create Copy builders.

  When copying large file trees, iterating and creating Copy
  builders manually by computing the destination path can be a
  hassle. This convenience function provides a condensed mean to
  express common file trees copies, and returns the list of copied
  nodes.

  The sources nodes are copied in the to directory. The sources path
  is kept and concatenated to the destination directory. That is,
  copying 'foo/bar' into 'baz/quux' whill create the
  'baz/quux/foo/bar' node.

  If strip_prefix is specified, it is stripped from the source
  pathes before copying. That is, copying 'foo/bar/baz' into 'quux'
  with a strip prefix of 'foo' wil create the 'bar/baz/quux' node.

  sources      -- List of nodes to copy, or a single node to copy.
  to           -- Path where to copy.
  strip_prefix -- Prefix Path stripped from source pathes.

  >>> sources = [node('/tmp/.drake.copy.source/a'),
  ...            node('/tmp/.drake.copy.source/b')]
  >>> targets = copy(sources, '/tmp/.drake.copy.dest',
  ...                strip_prefix = '/tmp')
  >>> targets
  [/tmp/.drake.copy.dest/.drake.copy.source/a, /tmp/.drake.copy.dest/.drake.copy.source/b]
  """
  return __copy(sources, to, strip_prefix, Copy)


def install(sources, to, strip_prefix = None):
  """Convenience function to create Install builders.

  See documentation of copy.
  """
  return __copy(sources, to, strip_prefix, Install)


class Rule(VirtualNode):

    """Virtual node that bounces to other nodes.

    Since rules are virtual nodes, creating an install rule as
    demonstrated below would enable to run `drake //install' to
    copy files.

    >>> sources = nodes('/tmp/.drake.rule1', '/tmp/.drake.rule2')
    >>> for source in sources:
    ...     source.path().touch()
    >>> targets = copy(sources, '/tmp/.drake.rule.dest',
    ...                strip_prefix = '/tmp')
    >>> for target in targets:
    ...     target.path().remove()
    >>> rule = Rule('install', targets)
    >>> rule.build()
    Copy /tmp/.drake.rule.dest/.drake.rule2
    Copy /tmp/.drake.rule.dest/.drake.rule1
    """

    def __init__(self, name, nodes = []):
        """Create a rule.

        name  -- Node name.
        nodes -- The node to build when the rule is built
        """
        VirtualNode.__init__(self, name)
        class RuleBuilder(Builder):
            def execute(self):
                return True
            @property
            def command(self):
                return None
            def __str__(self):
                return 'RuleBuilder(%s)' % self._Builder__targets[0]
        RuleBuilder(nodes, [self])

    def hash(self):
        """Hash value."""
        return ''

    def __lshift__(self, nodes):
        """Add a node to build when the rule is built."""
        if isinstance(nodes, list):
            for node in nodes:
                self << node
        else:
            self.builder.add_src(nodes)


class EmptyBuilder(Builder):

    """Builder which execution does nothing.

    Usefull to create dependencies between nodes.
    """

    def execute(self):
        """Do nothing."""
        return True


class TouchBuilder(Builder):

    """Builder that simply creates its targets as empty files.

    >>> n = node('/tmp/.drake.touchbuilder')
    >>> n.path().remove()
    >>> b = TouchBuilder(n)
    >>> n.build()
    Touch /tmp/.drake.touchbuilder
    >>> n.path().exists()
    True
    """

    def __init__(self, nodes):
        """Create a TouchBuilder.

        nodes -- target nodes list, or a single target node.
        """
        if isinstance(nodes, BaseNode):
            nodes = [nodes]
        for node in nodes:
            assert isinstance(node, Node)
        Builder.__init__(self, [], nodes)

    def execute(self):
        """Create all the non-existent target nodes as empty files."""
        self.output('Touch %s' % ', '.join(map(str, self.targets())))
        for node in self.targets():
            assert isinstance(node, Node)
            node.path().touch()
        return True

# Architectures
class arch:

    """Architectures enum."""

    x86 = 0

# OSes
class os:

    """Oses enum."""

    android = 0
    linux = 1
    macos = 2
    windows = 3


def reset():
    for node in BaseNode.nodes.values():
        if node.builder is not None:
            node.builder._Builder__built = False
            node.builder._Builder__dynsrc = {}
    BaseNode.nodes = {}

# Configuration
class Configuration:

  def _search(self, what, where):
    return self._search_all(what, where)[0]

  def _search_all(self, what, where):
    what = Path(what)
    res = []
    for root in where:
      if (root / what).exists():
        res.append(root)
    if len(res) > 0:
      return res
    raise Exception('Unable to find %s in %s.' % \
                    (what, self._format_search(where)))

  def _search_many_all(self, whats, where):
    res = []
    for what in whats:
      try:
        res += [(res, what) for res in self._search_all(what, where)]
      except:
        pass
    if len(res) == 0:
      raise Exception('Unable to find %s in %s.' % \
                      (self._format_search(whats),
                       self._format_search(where)))
    return res

  def _format_search(self, where):
    if not isinstance(where, list):
      return str(where)
    elif len(where) <= 1:
      return str(where[0])
    else:
      return 'any of %s and %s' % (', '.join(map(str, where[:-1])),
                                   where[-1])

  def __search_version(self, what, where, major, minor, subminor):
    """ """
    if major is not None:
      if minor is not None:
        if subminor is not None:
          try:
            what.extension = 'so.%s.%s.%s' % (major, minor, subminor)
            return self._search(what, where) / what
          except:
            pass
        try:
          what.extension = 'so.%s.%s' % (major, minor)
          return self._search(what, where) / what
        except:
          pass
      try:
        what.extension = 'so.%s' % (major)
        return self._search(what, where) / what
      except:
        pass
    what.extension = 'so'
    return self._search(what, where) / what

  def _search_lib(self, what, where, major, minor, subminor):
    """ """
    path = self.__search_version(what, where, major, minor, subminor)


class Range:

    """A numeric range."""

    def __init__(self, inf, sup = True):
        """Create a numeric range with the given boundaries

        inf -- the inferior boundary.
        sup -- the superior boundary. If unspecified, equals the
               inferior boundary. If None, there is no upper bound
               to the range (it includes any number superior or
               equal to inf).

        >>> 4 in Range(5)
        False
        >>> 5 in Range(5)
        True
        >>> 6 in Range(5)
        False

        >>> 4 in Range(5, 7)
        False
        >>> 5 in Range(5, 7)
        True
        >>> 6 in Range(5, 7)
        True
        >>> 7 in Range(5, 7)
        True
        >>> 8 in Range(5, 7)
        False
        >>> 42 in Range(5, None)
        True
        """
        if isinstance(inf, Range):
            assert sup is True
            sup = inf.sup()
            inf = inf.inf()
        assert inf is not None
        self.__inf = inf
        if sup is True:
            sup = inf
        self.__sup = sup

    def sup(self):
        return self.__sup

    def inf(self):
        return self.__inf

    def __contains__(self, val):
      """Whether val is included in self."""
      if isinstance(val, Range):
        return val.inf() in self
      sup = (self.__sup is None or val <= self.__sup)
      return val >= self.__inf and sup

    def __eq__(self, rhs):
        return self.__inf == rhs.__inf and self.__sup == rhs.__sup

    def __ge__(self, rhs):
        return self.__inf >= rhs.__sup

    def __gt__(self, rhs):
        return self.__inf > rhs.__sup

    def __str__(self):
        """A visual representation of the range.

        >>> str(Range(5))
        '5'
        >>> str(Range(5, 7))
        '[5, 7]'
        >>> str(Range(5, None))
        '[5, ...]'
        """
        if self.__sup == self.__inf:
            return str(self.__inf)
        elif self.__sup is None:
            return '[%s, ...]' % self.__inf
        return '[%s, %s]' % (self.__inf, self.__sup)

    def __repr__(self):
        if self.__sup == self.__inf:
            return 'Range(%s)' % self.__inf
        elif self.__sup is None:
            return 'Range(%s, None)' % self.__inf
        return 'Range(%s, %s)' % (self.__inf, self.__sup)

class Version:

    def __init__(self, major = None, minor = None, subminor = None):
        assert major is not None or minor is None and subminor is None
        assert minor is not None or subminor is None
        self.__major = major and Range(major)
        self.__minor = minor and Range(minor)
        self.__subminor = subminor and Range(subminor)

    @property
    def major(self):
        return self.__major

    @property
    def minor(self):
        return self.__minor

    @property
    def subminor(self):
        return self.__subminor

    def __str__(self):
        if self.__major is not None:
            if self.__minor is not None:
                if self.__subminor is not None:
                    return '%s.%s.%s' % (self.__major, self.__minor,
                                         self.__subminor)
                else:
                    return '%s.%s' % (self.__major, self.__minor)
            else:
                return '%s' % (self.__major)
        else:
            return 'any version'

    def __contains__(self, other):
      """Whether a version includes another.

      >>> Version(1, 2, 3) in Version(1, 2, 3)
      True
      >>> Version(1, 2, 2) in Version(1, 2, 3)
      False
      >>> Version(1, 2, 4) in Version(1, 2, 3)
      False
      >>> Version(1, 2) in Version(1, 2, 3)
      False
      >>> Version(1, 2, 3) in Version(1, 2)
      True
      >>> Version(1, 3) in Version(1, Range(2, 4))
      True
      >>> Version(1, 2, 3) in Version()
      True
      """
      if self.__major is not None:
        if other.__major is None or \
           not other.__major in self.__major:
          return False
        if self.__minor is not None:
          if other.__minor is None or \
             not other.__minor in self.__minor:
            return False
          if self.__subminor is not None:
            if other.__subminor is None or \
               not other.__subminor in self.__subminor:
              return False
      return True

    def __ge__(self, rhs):
        """Whether a version is greater than another.

        >>> Version(1, 2, 3) >= Version(1, 2, 3)
        True
        >>> Version(1, 2, 4) >= Version(1, 2, 3)
        True
        >>> Version(1, 3, 2) >= Version(1, 2, 3)
        True
        >>> Version(2, 0, 0) >= Version(1, 10, 23)
        True
        >>> Version(1, 2, 3) >= Version(1, 2, 4)
        False
        >>> Version(1, 2, 3) >= Version(1, 3, 2)
        False
        """
        assert self.__major is not None and rhs.__major is not None
        if self.__major == rhs.__major:
            minor = self.__minor or 0
            rhs_minor = rhs.__minor or 0
            if minor == rhs_minor:
                subminor = self.__subminor or 0
                rhs_subminor = rhs.__subminor or 0
                return subminor >= rhs_subminor
            else:
                return minor > rhs_minor
        else:
            return self.__major > rhs.__major


def reset():
    BaseNode.nodes = {}

class Runner(Builder):

  class Reporting(Enumerated,
                  values = ['always', 'never', 'on_failure']):
    pass

  def __init__(self, exe, args = None, env = None):
    self.__args = args or list()
    self.__exe = exe
    self.__out = node('%s.out' % exe.name())
    self.__err = node('%s.err' % exe.name())
    self.__status = node('%s.status' % exe.name())
    self.__sources = [exe]
    self.__env = env
    if isinstance(exe, cxx.Executable):
        self.__sources += exe.dynamic_libraries
    self.stdout_reporting = Runner.Reporting.never
    self.stderr_reporting = Runner.Reporting.always
    Builder.__init__(self,
                     self.__sources,
                     [self.__out, self.__err, self.__status])

  @property
  def status(self):
    return self.__status

  def __reporting_set(self, val):
    self.stdout_reporting = val
    self.stderr_reporting = val
  reporting = property(fget = None, fset = __reporting_set)

  def __must_report(self, reporting, status):
    if reporting is Runner.Reporting.always:
      return True
    elif reporting is Runner.Reporting.on_failure:
      return status != 0
    else:
      return False

  def __report(self, node):
    with open(str(node.path()), 'r') as f:
      for line in f:
        print('  %s' % line, end = '')

  def execute(self):
    import subprocess
    with open(str(self.__out.path()), 'w') as out, \
         open(str(self.__err.path()), 'w') as err, \
         open(str(self.__status.path()), 'w') as rv:
      self.output(' '.join(self.command),
                  'Run %s' % self.__exe)
      try:
        p = subprocess.Popen(self.command,
                             stdout = out,
                             stderr = err,
                             env = self.__env)
        p.wait()
        status = p.returncode
        print(status, file = rv)
      except:
        import traceback
        traceback.print_exception(*sys.exc_info(), file = err)
        return False
    if self.__must_report(self.stdout_reporting, status):
      self.__report(self.__out)
    if self.__must_report(self.stderr_reporting, status):
      self.__report(self.__err)
    return status == 0

  @property
  def command(self):
    path = str(self.__exe.path())
    if not self.__exe.path().absolute():
      path = './%s' % path
    return [str(path)] + list(map(str, self.__args))

  @property
  def executable(self):
    return self.__exe

  def __str__(self):
    return str(self.__exe)


class TestSuite(Rule):

  def __init__(self, *args, **kwargs):
    Rule.__init__(self, *args, **kwargs)
    self.__success = 0
    self.__failures = 0

  @property
  def success(self):
    return self.__success

  @property
  def failures(self):
    return self.__failures

  @property
  def total(self):
    return self.success + self.failures

  def report_dependencies(self, deps):
    failures = []
    for dep in deps:
      if dep.build_status:
        self.__success += 1
      else:
        failures.append(dep)
        self.__failures += 1
    self.builder.output('%s: %s / %s tests passed.' %
                        (self, self.success, self.total))

  def __str__(self):
    return 'Test suite %s' % self.name()


class HTTPDownload(Builder):

  def __init__(self, url, dest, fingerprint = None):
    self.__url = url
    self.__dest = dest
    self.__fingerprint = fingerprint
    Builder.__init__(self, [], [self.__dest])

  def execute(self):
    self.output('Download %s to %s' % (self.__url, self.__dest),
                'Download %s' % self.__dest)
    import httplib2
    h = httplib2.Http()
    resp, content = h.request(self.__url, "GET")
    status = resp['status']
    if status != '200':
      print('download failed with status %s' % status,
            file = sys.stderr)
      return False
    if self.__fingerprint is not None:
      import hashlib
      d = hashlib.md5()
      d.update(content)
      if d.hexdigest() != self.__fingerprint:
        print('checksum failed', file = sys.stderr)
        return False
    with open(str(self.__dest.path()), 'wb') as f:
      f.write(content)
    return True


class TarballExtractor(Builder):

  def __init__(self, tarball, targets = []):
    self.__tarball = tarball
    import tarfile
    directory = self.__tarball.name().dirname()
    self.__targets = [node(self.__tarball.name().dirname() / target)
                      for target in targets]
    # targets = []
    # with tarfile.open(str(self.__tarball.path()), 'r') as f:
    #   for name in f.getnames():
    #     targets.append(directory / name)
    # for target in targets:
    #   print(target)
    # self.__targets = nodes(*targets)
    Builder.__init__(self, [tarball], self.__targets)

  def execute(self):
    import tarfile
    self.output('Extract %s' % self.__tarball)
    with tarfile.open(str(self.__tarball.path()), 'r') as f:
      f.extractall(str(self.__tarball.path().dirname()))
    return True
