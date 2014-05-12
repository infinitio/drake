#!/usr/bin/env python3

# Ensure this drake is used
import os.path
import sys
SELF = os.path.realpath(__file__)
sys.path = [os.path.dirname(os.path.dirname(SELF))] + sys.path

import sched
import unittest

class BeaconException(Exception):
  pass

class TestStandaloneCoroutine(unittest.TestCase):

  def setUp(self):
    self.beacon = 0

  def coroutine(self, n):
    self.beacon = n
    sched.coro_yield()
    self.beacon = n + 1

  def coroutine_meta(self, n):
    self.beacon = n
    sched.coro_yield()
    self.coroutine(n + 1)
    sched.coro_yield()
    self.beacon = n + 3

  def coroutine_wait(self, coro, n):
    sched.wait(coro)
    self.beacon = n

  def coroutine_raise(self):
    self.function_raise()

  def function_raise(self):
    raise BeaconException('exn')

  def coroutine_raise_meta(self):
    sched.coro_yield()
    self.coroutine_raise()
    sched.coro_yield()

  def test_one(self):
    self.c = sched.Coroutine(lambda: self.coroutine(1), 'coro')
    self.assertFalse(self.c.done)
    self.assertEqual(self.beacon, 0)
    self.c.step()
    self.assertFalse(self.c.done)
    self.assertEqual(self.beacon, 1)
    self.c.step()
    self.assertTrue(self.c.done)
    self.assertEqual(self.beacon, 2)
    with self.assertRaises(sched.CoroutineDone):
      self.c.step()

  def test_two(self):
    self.c1 = sched.Coroutine(lambda: self.coroutine(1), 'coro1')
    self.c2 = sched.Coroutine(lambda: self.coroutine(2), 'coro2')
    self.assertEqual(self.beacon, 0)
    self.c1.step()
    self.assertEqual(self.beacon, 1)
    self.c2.step()
    self.assertEqual(self.beacon, 2)
    self.c2.step()
    self.assertEqual(self.beacon, 3)
    self.c1.step()
    self.assertEqual(self.beacon, 2)
    with self.assertRaises(sched.CoroutineDone):
      self.c1.step()
    with self.assertRaises(sched.CoroutineDone):
      self.c2.step()

  def test_run(self):
    self.c = sched.Coroutine(lambda: self.coroutine(1), 'coro')
    self.assertFalse(self.c.done)
    self.assertEqual(self.beacon, 0)
    self.c.run()
    self.assertTrue(self.c.done)
    self.assertEqual(self.beacon, 2)
    with self.assertRaises(sched.CoroutineDone):
      self.c.step()

  def test_recursive(self):
    self.c = sched.Coroutine(lambda: self.coroutine_meta(1), 'coro')
    self.assertEqual(self.beacon, 0)
    self.c.step()
    self.assertEqual(self.beacon, 1)
    self.c.step()
    self.assertEqual(self.beacon, 2)
    self.c.step()
    self.assertEqual(self.beacon, 3)
    self.c.step()
    self.assertEqual(self.beacon, 4)
    with self.assertRaises(sched.CoroutineDone):
      self.c.step()

  def test_wait(self):
    self.c = sched.Coroutine(lambda: self.coroutine(1), 'coro')
    self.w = sched.Coroutine(lambda: self.coroutine_wait(self.c, 3), 'coro')
    self.assertFalse(self.c.done)
    self.assertFalse(self.w.done)
    self.assertFalse(self.w.frozen)
    self.w.step()
    self.assertTrue(self.w.frozen)
    with self.assertRaises(sched.CoroutineFrozen):
      self.w.step()
    self.assertEqual(self.beacon, 0)
    self.c.step()
    self.assertEqual(self.beacon, 1)
    self.assertTrue(self.w.frozen)
    with self.assertRaises(sched.CoroutineFrozen):
      self.w.step()
    self.c.step()
    self.assertEqual(self.beacon, 2)
    self.assertTrue(self.c.done)
    self.assertFalse(self.w.frozen)
    self.w.step()
    self.assertEqual(self.beacon, 3)
    self.assertTrue(self.w.done)

  def test_exception(self):
    self.r = sched.Coroutine(self.coroutine_raise, 'coro')
    with self.assertRaises(BeaconException):
      self.r.step()
    self.assertTrue(self.r.done)

  def test_exception_recurse(self):
    self.r = sched.Coroutine(lambda: self.coroutine_raise_meta(), 'coro')
    self.r.step()
    with self.assertRaises(BeaconException):
      self.r.step()
    self.assertTrue(self.r.done)


class Sleep(sched.ThreadedOperation):
  def __init__(self, duration):
    sched.ThreadedOperation.__init__(self)
    self.__duration = duration

  def run(self):
    import time
    time.sleep(self.__duration)

class TestScheduler(unittest.TestCase):

  def __init__(self, *args, **kwargs):
    unittest.TestCase.__init__(self, *args, **kwargs)

  def setUp(self):
    self.scheduler = sched.Scheduler()
    self.beacon1 = 0
    self.beacon2 = 0

  def coroutine1(self):
    self.beacon1 += 1
    sched.coro_yield()
    self.beacon1 += 1

  def coroutine2(self):
    self.beacon2 += 1
    sched.coro_yield()
    self.beacon2 += 1

  def coroutine_wait(self, w):
    #self.assertEqual(self.beacon1, 1)
    sched.Coroutine.current.wait(w)
    #self.assertEqual(self.beacon1, 2)

  def test_basic(self):
    sched.Coroutine(self.coroutine1, 'coro', self.scheduler)
    self.assertEqual(self.beacon1, 0)
    self.scheduler.run()
    self.assertEqual(self.beacon1, 2)

  def test_several(self):
    sched.Coroutine(self.coroutine1, 'coro1', self.scheduler)
    sched.Coroutine(self.coroutine2, 'coro2', self.scheduler)
    self.assertEqual(self.beacon1, 0)
    self.assertEqual(self.beacon2, 0)
    self.scheduler.run()
    self.assertEqual(self.beacon1, 2)
    self.assertEqual(self.beacon2, 2)

  def test_wait(self):
    c1 = sched.Coroutine(self.coroutine1, 'coro1', self.scheduler)
    cw = sched.Coroutine(lambda: self.coroutine_wait(c1), 'wait',
                   self.scheduler)
    self.scheduler.run()

  def test_reactor(self):

    s = Sleep(1)
    cw = sched.Coroutine(lambda: self.coroutine_wait(s), 'wait',
                   self.scheduler)
    sleeper = sched.Coroutine(s.start, 'coro_sleep', self.scheduler)
    self.scheduler.run()

  def test_fwd_exception(self):

    def coro():
      def subcoro():
        def raiser():
          raise BeaconException()
        with sched.Scope() as scope:
          scope.run(raiser, 'raiser')
      with self.assertRaises(BeaconException):
        with sched.Scope() as scope:
          scope.run(subcoro, 'subcoro')

    c = sched.Coroutine(coro, 'coro', self.scheduler)
    self.scheduler.run()

  def test_semaphore_simple(self):
    s = sched.Semaphore(1)
    def lock_f():
      with s:
        pass
    lock = sched.Coroutine(lock_f, 'lock', self.scheduler)
    self.scheduler.run()
    assert s.count == 1

  def test_semaphore(self):

    s = sched.Semaphore(1)
    beacon = [0]
    def lock_f(beacon):
      for i in range(3):
        s.lock()
        beacon[0] = beacon[0] + 1
    lock = sched.Coroutine(lambda: lock_f(beacon), 'lock', self.scheduler)
    def read_f(beacon):
      def check(i):
        # Yield twice, to make sure lock_f has an execution slot: we
        # just woke him, so it might be scheduled after us in the next
        # round.
        sched.coro_yield()
        sched.coro_yield()
        assert beacon[0] == i
        sched.coro_yield()
        assert beacon[0] == i
        s.unlock()
        assert beacon[0] == i
      check(1)
      check(2)
      check(3)
    read = sched.Coroutine(lambda: read_f(beacon), 'read', self.scheduler)
    self.scheduler.run()

  def test_continue_raise(self):

    def thrower():
      raise BeaconException()

    def waiter(beacon):
      sched.coro_yield()
      sched.coro_yield()
      sched.coro_yield()
      beacon[0] += 1

    def main():
      beacon = [0]
      try:
        with sched.Scope() as scope:
          scope.run(thrower, 'thrower')
          scope.run(lambda: waiter(beacon), 'waiter')
      finally:
        assert beacon[0] == 0

    sched.Coroutine(main, 'main', self.scheduler)
    try:
      self.scheduler.run()
    except BeaconException:
      pass
    else:
      assert False

  def test_break_scope(self):
    beacon = [0]
    def incrementer(beacon):
      while True:
        beacon[0] += 1
        sched.coro_yield()
    def main():
      with sched.Scope() as scope:
        scope.run(lambda: incrementer(beacon), 'incrementer')
        sched.coro_yield()
        sched.coro_yield()
        raise BeaconException()
    scheduler = sched.Scheduler()
    sched.Coroutine(main, 'main', scheduler)
    try:
      scheduler.run()
    except BeaconException:
      assert beacon[0] == 1
    else:
      assert False

  def test_terminate_starting(self):
    beacon = [0]
    def incrementer(beacon):
      beacon[0] += 1
    def main():
      with sched.Scope() as scope:
        scope.run(lambda: incrementer(beacon), 'incrementer')
        raise BeaconException()
    scheduler = sched.Scheduler()
    sched.Coroutine(main, 'main', scheduler)
    try:
      scheduler.run()
    except BeaconException:
      assert beacon[0] == 0
    else:
      assert False

unittest.main()
