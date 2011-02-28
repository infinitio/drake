# Copyright (C) 2009-2011, Quentin "mefyl" Hocquet
#
# This software is provided "as is" without warranty of any kind,
# either expressed or implied, including but not limited to the
# implied warranties of fitness for a particular purpose.
#
# See the LICENSE file for more information.

import os, hashlib, platform, re, subprocess, sys, threading, time, types, shutil
from copy import deepcopy

def clone(o):

    return deepcopy(o)


class Exception(Exception):

    pass


DEBUG = 0
if 'DRAKE_DEBUG' in os.environ:
    DEBUG = int(os.environ['DRAKE_DEBUG'])
DEBUG_TRACE = 1
DEBUG_TRACE_PLUS = 2
DEBUG_DEPS = 2
DEBUG_SCHED = 3

INDENT = 0

RAW = 'DRAKE_RAW' in os.environ
SILENT = 'DRAKE_SILENT' in os.environ


class Scheduler:

    def __init__(self, jobs = 1):

        self.__coroutines = []
        self.waiting_coro_lock = threading.Semaphore(0)
        self.ncoro = 0
        self.jobs = jobs
        self.__running = False
        self.__exception = None
        self.__sem = threading.Semaphore(1)
        self.__local = threading.local()
        self.__local.i = -1

    def running(self):

        return self.__running

    def add(self, coro):

        with self.__sem:
            debug('%s: new coroutine: %s' % (self.__local.i, coro.name), DEBUG_SCHED)
        self.__coroutines.append(coro)
        self.ncoro += 1
        self.waiting_coro_lock.release()

    def woken_up(self, coro):
        with self.__sem:
            debug('%s: coroutine woke up: %s' % (self.__local.i, coro.name), DEBUG_SCHED)
        self.__coroutines.append(coro)
        self.waiting_coro_lock.release()


    def run(self):

        self.__running = True
        self.die = False

        def job(i):
            self.__local.i = i
            while True:
                # If there are no more coroutines
                with self.__sem:
                    if self.ncoro == 0 or self.__exception is not None:
                        if self.ncoro == 0:
                            debug('%s: no more coroutine, dying' % self.__local.i, DEBUG_SCHED)
                        else:
                            debug('%s: pending exception, dying' % self.__local.i, DEBUG_SCHED)
                        # Tell all jobs they must die
                        self.die = True
                        # Wake everyone
                        for i in range(self.jobs - 1):
                            self.waiting_coro_lock.release()
                        # Quit
                        break

                # Lock one coroutine slot
                if not self.waiting_coro_lock.acquire(False):
                    debug('%s: no more coroutine available, sleeping' % self.__local.i, DEBUG_SCHED)
                    self.waiting_coro_lock.acquire()
                    debug('%s: woken up' % self.__local.i, DEBUG_SCHED)
                # If we must die, do so
                if self.die:
                    with self.__sem:
                        debug('%s: scheduler is dying, dying too' % self.__local.i, DEBUG_SCHED)
                    return
                # Fetch our coroutine
                with self.__sem:
                    coro = self.__coroutines[-1]
                    del self.__coroutines[-1]
                res = None
                try:
                    # Run one step of our coroutine
                    with self.__sem:
                        debug('%s: step %s' % (self.__local.i, coro.name), DEBUG_SCHED)
                    res = coro.step()
                except Exception, e:
                    self.__exception = sys.exc_info()
                with self.__sem:
                    if res:
                        if coro.frozen():
                            debug('%s: coroutine froze: %s' % (self.__local.i, coro.name), DEBUG_SCHED)
                        else:
                            debug('%s: pushing coroutine back: %s' % (self.__local.i, coro.name), DEBUG_SCHED)
                            self.__coroutines.append(coro)
                            self.waiting_coro_lock.release()
                    else:
                        debug('%s: coroutine ended: %s' % (self.__local.i, coro.name), DEBUG_SCHED)
                        self.ncoro -= 1
        if JOBS == 1:
            job(0)
        else:
            threads = [threading.Thread(target = job, args=[i]) for i in range(self.jobs)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.__running = False
        if self.__exception is not None:
            raise self.__exception[1], None, self.__exception[2]

class Coroutine:

    def __init__(self, routine, name):

        self.routine = [routine]
        self.name = name
        self.__done = False
        self.__frozen = False
        self.__waiters = []
        scheduler().add(self)

    def frozen(self):

        return self.__frozen

    def __str__(self):

        return 'coro %s' % self.name

    def step(self):

        if not self.__frozen:
            while self._step():
                if not self.routine:
                    self.__done = True
                    for coro in self.__waiters:
                        coro.__frozen = False
                        scheduler().woken_up(coro)
                    return False
        return True

    def done(self):

        return self.__done

    def _step(self):

        try:
            value = self.routine[-1].next()
            if isinstance(value, types.GeneratorType):
                self.routine.append(value)
                return True
            if isinstance(value, Coroutine) and not value.done():
                self.__frozen = True
                value.__waiters.append(self)
                return False
            else:
                return False
        except StopIteration:
            del self.routine[-1]
            return True

__debug_sem = threading.Semaphore(1)

def debug(msg, lvl = 1):

    global DEBUG
    global INDENT
    if lvl <= DEBUG:
        with __debug_sem:
            print >> sys.stderr, '%s%s' % (' ' * INDENT * 2, msg)

class indentation:
    def __enter__(self):
        global INDENT
        INDENT += 1
    def __exit__(self, type, value, traceback):
        global INDENT
        INDENT -= 1

class Path:

    separator = '/'
    if platform.system() == 'Windows':
        separator = '\\'

    def __init__(self, path):

        self.absolute = False
        if path.__class__ == list:
            self.path = path
        elif path.__class__ == Path:
            self.path = clone(path.path)
            self.absolute = path.absolute
        else:
            assert path
            if platform.system() == 'Windows':
                self.path = re.split(r'/|\\', path)
                self.absolute = bool(self.path[0] == '' or re.compile('^[A-Z]:').match(self.path[0]))
            else:
                self.path = path.split('/')
                self.absolute = self.path[0] == ''

    def __getattr__(self, name):

        if name == 'extension':
            parts = self.path[-1].split('.')
            if len(parts) > 1:
                return '.'.join(parts[1:])
            else:
                return ''
        return object.__getattr__(self, name)

    def __setattr__(self, name, value):

        if name == 'extension':
            parts = self.path[-1].split('.')
            if len(parts) > 1:
                if value == '':
                    parts = [parts[0]]
                else:
                    parts = [parts[0], value]
                self.path[-1] = '.'.join(parts)
            else:
                if value != '':
                    self.path[-1] += '.%s' % value
        else:
            self.__dict__[name] = value
        return value

    def extension_strip_last_component(self):

        self.extension = '.'.join(self.extension.split('.')[:-1])

    def __str__(self):

        return self.separator.join(self.path)

    def __repr__(self):

        return 'Path(\"%s\")' % str(self)

    def __lt__(self, rhs):

        return str(self) < str(rhs)

    def __hash__(self):

        return hash(str(self))

    def exists(self):

        return os.path.exists(str(self))

    def basename(self):

        return Path(self.path[-1:])

    def dirname(self):

        return Path(self.path[0:-1])

    def touch(self):

        self.dirname().mkpath()
        if not os.path.exists(str(self)):
            open(str(self), 'w').close()

    def mkpath(self):

        if not os.path.exists(str(self)):
            os.makedirs(str(self))

    def __eq__(self, rhs):

        if rhs.__class__ != Path:
            rhs = Path(rhs)
        return self.path == rhs.path

    def __div__(self, rhs):

        rhs = Path(rhs)

        if self == '.':
            return rhs
        if rhs == Path('.'):
            return clone(self)

        res = clone(self)
        res.path += rhs.path
        return res

    def strip_prefix(self, rhs):

        if (not isinstance(rhs, Path)):
            rhs = Path(rhs)
        if self.path[0:len(rhs.path)] != rhs.path:
            raise Exception("%s is not a prefix of %s" % (rhs, self))
        self.path = self.path[len(rhs.path):]
        if not self.path:
            self.path = ['.']

CACHEDIR = Path('.drake')

class DepFile:

    def __init__(self, builder, name):

        self.builder = builder
        self.name = name
        builder.targets().sort()
        self._files = {}
        self._sha1 = {}


    def files(self):

        return self._files.values()


    def sha1s(self):

        return self._sha1


    def register(self, node):

        self._files[str(node.path())] = node


    def path(self):

        return self.builder.cachedir() / self.name


    def read(self):

        res = []

        self.path().touch()
        for line in open(str(self.path()), 'r'):
            sha1 = line[:40]
            remain = line[41:-1].split(' ') # Chomp the \n
            src = Path(remain[0])
            self._sha1[str(src)] = (sha1, remain[1])

    def up_to_date(self):

        for path in self._sha1.keys():
            if path not in Node.nodes:
                del self._sha1[path]
                continue
            h = node(path).hash()
            if self._sha1[path][0] != h:
                debug('Execution needed because hash is outdated: %s.' % path, DEBUG_DEPS)
                return False

        return True


    def update(self):

        f = open(str(self.path()), 'w')
        for path in self._files:
            h = self._files[path].hash()
            print >>f, '%s %s %s' % (h, self._files[path].id(), self._files[path].drake_type())

    def __repr__(self):

        return 'DepFile(%s)' % repr(self.builder)

    def __str__(self):

        return 'DepFile(%s)' % self.builder

def path_build(path):
    return prefix() / path

def path_src(path):
    return srctree() / path_build(path)

class BaseNodeTypeType(type):

    node_types = {}

    def __call__(c, name, *arg):

        res = type.__call__(c, name, *arg)
        k = '%s.%s' % (res.__module__, res.__name__)
        BaseNodeTypeType.node_types[k] = res
        return res

        return type.__call__(*arg)

class BaseNodeType(type):

    __metaclass__ = BaseNodeTypeType

    def __call__(c, *args):

        try:
            return type.__call__(c, *args)
        except NodeRedefinition, e:
            assert e.sym in BaseNode.nodes
            node = BaseNode.nodes[e.sym]
            assert node.__class__ is c
            return node

class NodeRedefinition(Exception):

    def __init__(self, sym):

        Exception.__init__(self)
        self.sym = sym

    def __str__(self):

        return 'node redefinition: %s' % self.sym

class BaseNode(object):

    nodes = {}
    uid = 0
    extensions = {}

    __metaclass__ = BaseNodeType

    def __init__(self, sym_path, src_path):

        if str(src_path) in BaseNode.nodes:
            raise NodeRedefinition(str(src_path))
        self.sym_path = sym_path
        self.src_path = src_path
        self.uid = BaseNode.uid
        BaseNode.uid += 1
        self.builder = None
        self.srctree = srctree()
        BaseNode.nodes[str(self.id())] = self
        self.consumers = []

    def dot(self, marks):

        if (self in marks):
            return True
        marks[self] = None

        print '  node_%s [label="%s"]' % (self.uid, self.sym_path)
        if self.builder is not None:
            if self.builder.dot(marks):
                print '  builder_%s -> node_%s' % (self.builder.uid, self.uid)
        return True

    @classmethod
    def drake_type(self):
        return '%s.%s' % (self.__module__, self.__name__)

    def path(self):

        return self.src_path

    def __str__(self):

        return str(self.path())

    def id(self):

        return self.src_path

    def hash(self):

        raise Exception('hash must be implemented by BaseNodes')

    def build(self):

        if not scheduler().running():
            c = Coroutine(self.build_coro(), name = str(self))
            scheduler().run()
        else:
            for everything in self.build_coro():
                pass

    def build_coro(self):

        debug('Building %s.' % self, DEBUG_TRACE)
        with indentation():
            if self.builder is None:
                return

            if JOBS == 1:
                for everything in self.builder.run():
                    pass
            else:
                yield self.builder.run()

    def clean(self):

        pass


class VirtualNode(BaseNode):

    def __init__(self, name):

        BaseNode.__init__(self, '//%s' % name, '//%s' % (prefix() / name))

    def hash(self):

        raise Exception('hash must be implemented by VirtualNodes')


class Node(BaseNode):

    def __init__(self, path):

        self._hash = None
        if path.__class__ == str:
            path = Path(path)
        BaseNode.__init__(self, path, prefix() / path)


    def clone(self, path):

        return Node(path)


    def hash(self):

        self._hash = hashlib.sha1(open(str(self.path())).read()).hexdigest()
        return self._hash


    def clean(self):

        if self.builder is not None:
            self.builder.clean()
            if self.path().exists():
                print 'Deleting %s' % self
                os.remove(str(self.path()))


    def path(self):

        if self.src_path.absolute:
#            assert self.builder is None
            return self.src_path

        if self.builder is None:
            return self.srctree / self.src_path
        else:
            return self.src_path

    def name(self):

        return self.src_path

    def build(self):

        if not scheduler().running():
            c = Coroutine(self.build_coro(), name = str(self))
            scheduler().run()
        else:
            for everything in self.build_coro():
                pass

    def build_coro(self):

        debug('Building %s.' % self, DEBUG_TRACE)
        with indentation():
            if self.builder is None:
                if not self.path().exists():
                    raise Exception('no builder to make %s' % self)
                return

            if JOBS == 1:
                for everything in self.builder.run():
                    pass
            else:
                yield self.builder.run()


    def __setattr__(self, name, value):

        if name == 'builder' and 'builder' in self.__dict__:
            del self.nodes[str(self.id())]
            self.__dict__[name] = value
            self.nodes[str(self.id())] = self
        else:
            self.__dict__[name] = value


    def __repr__(self):

        return str(self.path())


    def __lt__(self, rhs):

        return self.path() < rhs.path()


    def produced_direct(self):

        if len(self.consumers) != 1 or len(self.consumers[0].dsts) != 1:
            return None
        else:
            return self.consumers[0].dsts[0]

def node(path, type = None):

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

    return map(node, paths)


def cmd_escape(fmt, *args):

    rg = re.compile('\'')

    args = map(str, args)
    for arg in args:
        if rg.match(arg):
            pass
    return fmt % tuple(args)


def cmd(fmt, *args):

    command = cmd_escape(fmt, *args)
    return os.system(command) == 0


def cmd_output(fmt, *args):

    command = cmd_escape(fmt, *args)
    return subprocess.Popen(command, shell=True, stdout=subprocess.PIPE).communicate()[0] # Chomp \n


class Builder:


    builders = []
    uid = 0

    name = 'build'
    _deps_handlers = {}

    @classmethod
    def register_deps_handler(self, name, f):
        self._deps_handlers[name] = f

    def __init__(self, srcs, dsts):

        assert srcs.__class__ == list
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
        self.__built = False
        self.__built_exception = None
        self.dynsrc = {}

    def sources(self):

        return self.__sources

    def targets(self):

        return self.__targets

    def cmd(self, pretty, c, *args):

        c = cmd_escape(c, *args)
        self.output(c, pretty)
        return cmd(c)

    def output(self, raw, pretty = None):

        if not SILENT:
            print (not RAW and pretty) or raw


    def cachedir(self):

        path = self.__targets[0].path()
        res = prefix() / path.dirname() / CACHEDIR / path.basename()
        res.mkpath()
        return res


    def dependencies(self):

        return []


    def depfile(self, name):

        if name not in self._depfiles:
            self._depfiles[name] = DepFile(self, name)
        return self._depfiles[name]


    def add_dynsrc(self, name, node, data = None):

        self.depfile(name).register(node)
        self.dynsrc[str(node.path())] = node


    def get_type(self, tname):

        return BaseNodeTypeType.node_types[tname]

    def run(self):

        global JOBS
        debug('Running %s.' % self, DEBUG_TRACE_PLUS)

        # If we were already executed, just skip
        if self.__built:
            if self.__built_exception is not None:
                raise self.__built_exception
            debug('Already built in this run.', DEBUG_TRACE_PLUS)
            return

        # The list of static dependencies is now fixed
        for path in self.__sources:
            self._depfile.register(self.__sources[path])

        # See Whether we need to execute or not
        execute = False

        # Reload dynamic dependencies
        if not execute:
            for f in os.listdir(str(self.cachedir())):
                if f == 'drake':
                    continue
                debug('Considering dependencies file %s' % f, DEBUG_DEPS)
                depfile = self.depfile(f)
                depfile.read()
                handler = self._deps_handlers[f]

                with indentation():
                    for path in depfile.sha1s():

                        if path in self.__sources or path in self.dynsrc:
                            debug('File %s is already in our sources.' % path, DEBUG_DEPS)
                            continue

                        if path in Node.nodes:
                            node = Node.nodes[path]
                        else:
                            debug('File %s is unknown, calling handler.' % path, DEBUG_DEPS)
                            node = handler(self, path, self.get_type(depfile.sha1s()[path][1]), None)

                        debug('Adding %s to our sources.' % node, DEBUG_DEPS)
                        self.add_dynsrc(f, node, None)


        coroutines = []

        # Build static dependencies
        debug('Build static dependencies')
        with indentation():
            for node in self.__sources.values() + self.__vsrcs.values():
                if node.builder is None or \
                        node.builder.__built:
                    continue
                if JOBS == 1:
                    for everything in node.build_coro():
                        pass
                else:
                    coroutines.append(Coroutine(node.build_coro(), name = str(node)))

        # Build dynamic dependencies
        debug('Build dynamic dependencies')
        with indentation():
            for path in self.dynsrc:
                try:
                    node = self.dynsrc[path]
                    if node.builder is None or \
                            node.builder.__built:
                        continue
                    if JOBS == 1:
                        for everything in node.build_coro():
                            pass
                    else:
                        coroutines.append(Coroutine(node.build_coro(), name = str(node)))
                except Exception, e:
                    debug('Execution needed because dynamic dependency couldn\'t be built: %s.' % path)
                    execute = True

        if JOBS != 1:
            for coro in coroutines:
                yield coro

        # If any target is missing, we must rebuild.
        if not execute:
            for dst in self.__targets:
                if not dst.path().exists():
                    debug('Execution needed because of missing target: %s.' % dst.path(), DEBUG_DEPS)
                    execute = True

        # Load static dependencies
        self._depfile.read()

        # If a new dependency appeared, we must rebuild.
        if not execute:
            for p in self.__sources:
                path = self.__sources[p].id()
                if path not in self._depfile._sha1:
                    debug('Execution needed because a new dependency appeared: %s.' % path, DEBUG_DEPS)
                    execute = True
                    break

        # Check if we are up to date wrt all dependencies
        if not execute:
            if not self._depfile.up_to_date():
                execute = True
            for f in self._depfiles:
                if not self._depfiles[f].up_to_date():
                    execute = True


        if execute:
            debug('Executing builder %s' % self, DEBUG_TRACE)

            # Regenerate dynamic dependencies
            self.dynsrc = {}
            self._depfiles = {}
            debug('Recomputing dependencies', DEBUG_TRACE_PLUS)
            with indentation():
                self.dependencies()

            debug('Rebuilding new dynamic dependencies', DEBUG_TRACE_PLUS)
            with indentation():
                for node in self.dynsrc.values():
                    for y in node.build_coro():
                        yield y

            if not self.execute():
                self.__built = True
                self.__built_exception = Exception('%s failed' % self)
                raise self.__built_exception
            for dst in self.__targets:
                if not dst.path().exists():
                    raise Exception('%s wasn\'t created by %s' % (dst, self))
            self._depfile.update()
            for name in self._depfiles:
                self._depfiles[name].update()
            self.__built = True
        else:
            self.__built = True
            debug('Everything is up to date.', DEBUG_TRACE_PLUS)


    def execute(self):

        raise Exception('execute is not implemented for %s' % self)


    def clean(self):

        for path in self.__sources:
            self.__sources[path].clean()


    def __str__(self):

        return self.__class__.__name__


    def add_src(self, src):

        self.__sources[str(src.path())] = src
        src.consumers.append(self)


    def add_virtual_src(self, src):

        self.__vsrcs[str(src.path())] = src
        # src.consumers.append(self)


    def all_srcs(self):

        res = []
        for src in self.__sources.values() + self.dynsrc.values():
            res.append(src)
            if src.builder is not None:
                res += src.builder.all_srcs()
        return res

    def dot(self, marks):

        if (self in marks):
            return True
        marks[self] = None

        print '  builder_%s [label="%s", shape=rect]' % (self.uid, self.__class__)
        for node in self.__sources.values() + self.dynsrc.values():
            if node.dot(marks):
                print '  node_%s -> builder_%s' % (node.uid, self.uid)
        return True


class ShellCommand(Builder):

    def __init__(self, srcs, dsts, fmt, *args):

        Builder.__init__(self, srcs, dsts)
        self.fmt = fmt
        self.args = args

    def execute(self):

        return self.cmd(self.fmt, *self.args)


class Dictionary(VirtualNode):

    def __init__(self, name, content = {}):

        VirtualNode.__init__(self, 'dictionaries/%s' % name)
        self.content = content

    def hash(self):

        # FIXME: sha1 of the string repr ain't optimal
        items = self.content.items()
        items.sort()
        return hashlib.sha1(str(items)).hexdigest()

    def __iter__(self):

        return iter(self.content.items())


class Expander(Builder):

    def __init__(self, dicts, source, target = None,
                 matcher = re.compile('@([a-zA-Z0-9_-]+)@'),
                 missing_fatal = True):

        if not isinstance(dicts, list):
            dicts = [dicts]

        if target is None:
            target = Path(source.sym_path)
            target.extension_strip_last_component()
            target = node(target)
        else:
            assert isinstance(target, BaseNode)

        Builder.__init__(self, [source] + dicts, [target])
        self.dicts = dicts
        self.matcher = matcher
        self.missing_fatal = missing_fatal
        self.source = source
        self.__target = target

    def execute(self):

        self.output('Expand %s to %s' % (self.source, self.__target),
                    'Expand %s' % self.__target)
        vars = {}
        for d in self.dicts:
            vars.update(dict(d))
        content = open(str(self.source.path()), 'r').read()
        for match in self.matcher.finditer(content):
            key = match.group(1)
            try:
                content = content.replace(match.group(0), str(vars[key]))
            except KeyError:
                if self.missing_fatal:
                    print 'Missing expansion: %s' % key
                    return False

        print >> open(str(self.__target.path()), 'w'), content
        return True

    def target(self):

        return self.__target

def shell_escape(s):

    # FIXME: escape only if needed
    # FIXME: complete that
    return '"%s"' % str(s).replace('"', '\\"')

_prefix = Path('.')

def prefix():
    return _prefix

_srctree = Path('.')

def set_srctree(path):

    global _srctree
    _srctree = Path(path)

def srctree():

    global _srctree
    return _srctree

def strip_srctree(path):

    global _srctree
    res = clone(path)
    if not path.absolute:
        res.strip_prefix(_srctree)
    return res

class Module:

    def __init__(self, globals):

        self.globals = globals

    def __getattr__(self, name):

        return self.globals[name]


def include(path, *args, **kwargs):

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
        raise Exception('cannot find %s or %s in %s' % (', '.join(names[:-1]), names[-1], path))
    res = raw_include(str(drakefile), *args, **kwargs)
    _prefix = previous_prefix
    return res


def raw_include(path, *args, **kwargs):

    g = {}
    execfile(str(srctree() / path), g)
    res = Module(g)
    res.configure(*args, **kwargs)
    return res

def dot(node, *filters):

    # FIXME: coro!
    node.build()
    marks = {}
    print 'digraph'
    print '{'
    node.dot(marks)
    print '}'

modes_ = {}

def all_if_none(nodes):

    # Copy it, since it will change during iteration. This shouldn't
    # be a problem, all newly inserted will be dependencies of the
    # already existing nodes. Right?
    if len(nodes):
        return nodes
    else:
        return list(Node.nodes.values())

def command_add(name, action):
    modes_[name] = action

def build(nodes):
    if not len(nodes):
        nodes = [node for node in Node.nodes.values() if not len(node.consumers)]
    if JOBS == 1:
        for node in all_if_none(nodes):
            for everything in node.build_coro():
                pass
    else:
        for node in all_if_none(nodes):
            Coroutine(node.build_coro(), name = str(node))
        scheduler().run()
command_add('build', build)

def clean(nodes):
    for node in all_if_none(nodes):
        node.clean()
command_add('clean', clean)

def dot_cmd(nodes):
    for node in all_if_none(nodes):
        dot(node)
command_add('dot', dot_cmd)

def dot_show_cmd(nodes):
    if not len(nodes):
        print '%s: dot-show: give me some nodes to show.' % sys.argv[0]
    for node in nodes:
        p = subprocess.Popen('dot -Tpng | xv -', shell = True, stdin = subprocess.PIPE)
        stdout = sys.stdout
        sys.stdout = p.stdin
        dot(node)
        p.communicate()
        sys.stdout = stdout

command_add('dot-show', dot_show_cmd)

JOBS = 1
SCHEDULER = None

def scheduler():
    global JOBS, SCHEDULER
    if SCHEDULER is None:
        SCHEDULER = Scheduler(JOBS)
    return SCHEDULER

def jobs_set(n):
    global JOBS, SCHEDULER
    assert SCHEDULER is None
    JOBS = int(n)

options = {
    '--jobs': jobs_set,
    '-j'    : jobs_set,
}

def run(root, *cfg, **kwcfg):

    try:

        print '%s: Entering directory `%s\'' % (sys.argv[0], os.getcwd())
        set_srctree(root)
        root = raw_include('drakefile', *cfg, **kwcfg)

        args = sys.argv[1:]

        mode = modes_['build']
        i = 0

        while True:

            if i < len(args):
                arg = args[i]

                if arg in options:
                    options[arg](args[i + 1])
                    i += 2
                    continue

                if arg[0:2] == '--':

                    arg = arg[2:]

                    if arg in modes_:
                        mode = modes_[arg]
                    else:
                        raise Exception('Unknown option: %s.' % arg)
                    i += 1

            nodes = []
            while i < len(args) and args[i][0:2] != '--':
                nodes.append(node(args[i]))
                i += 1
            mode(nodes)

            if i == len(args):
                break

    except Exception, e:
        print '%s: %s' % (sys.argv[0], e)
        exit(1)
    except KeyboardInterrupt:
        print '%s: interrupted.' % sys.argv[0]
        exit(1)
    print '%s: Leaving directory `%s\'' % (sys.argv[0], os.getcwd())


class Copy(Builder):

    def __init__(self, fr, to, strip_prefix = None):

        self.__from = fr
        stripped = Path(fr.src_path)
        if strip_prefix is not None:
            stripped.strip_prefix(strip_prefix)
        self.__to = fr.clone(Path(to) / stripped)
        self.__to.builder = None
        Builder.__init__(self, [self.__from], [self.__to])

    def to(self):

        return self.__to

    def execute(self):

        self.output('Copy %s to %s' % (self.__from, self.__to),
                    'Copy %s' % self.__to,)
        # FIXME: errors!
        shutil.copyfile(str(self.__from), str(self.__to))
        return True


def copy(fr, to, strip_prefix = None):

    if isinstance(fr, list):
        res = []
        for node in fr:
            res.append(copy(node, to, strip_prefix))
        return res
    else:
        return Copy(fr, to, strip_prefix = strip_prefix).to()

class Rule(VirtualNode):

    def __init__(self, name, nodes = []):

        VirtualNode.__init__(self, name)
        class RuleBuilder(Builder):
            def run(self):
                debug('Build static dependencies')
                coroutines = []
                with indentation():
                    for node in self.sources().values():
                        if JOBS == 1:
                            for everything in node.build_coro():
                                pass
                        else:
                            coroutines.append(Coroutine(node.build_coro(), name = str(node)))
                if JOBS != 1:
                    for coro in coroutines:
                        yield coro

        RuleBuilder([], [self])

    def hash(self):

        return ''

    def __lshift__(self, nodes):

        if isinstance(nodes, list):
            for node in nodes:
                self << node
        else:
            self.builder.add_src(nodes)

# Architectures
x86 = 0

# OSes
android = 0
linux = 1
macos = 2
windows = 3

