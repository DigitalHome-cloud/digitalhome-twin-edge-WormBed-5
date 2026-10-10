# SPDX-License-Identifier: GPL-3.0-or-later
"""Run every callback that is ready, then return: one Webots step must not leave messages waiting.
rclpy's spin_once runs at most one callback, and rclpy has no spin_some."""
import time

from rclpy.executors import SingleThreadedExecutor


class Drain:
    def __init__(self, node):
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(node)
        self.calls = 0

    def wrap(self, fn):
        def counted(*args):
            self.calls += 1
            return fn(*args)
        return counted

    def __call__(self, limit=500):
        for _ in range(limit):
            before = self.calls
            self.executor.spin_once(timeout_sec=0)
            if self.calls == before:
                return

    def until(self, done, timeout_s):
        """Spin until done() is true or timeout_s (wall clock) has passed. Returns done()."""
        end = time.monotonic() + timeout_s
        while not done():
            left = end - time.monotonic()
            if left <= 0:
                return False
            self.executor.spin_once(timeout_sec=min(left, 0.01))
        return True
