# This is free and unencumbered software released into the public domain.
# (full license text omitted for brevity - same as original)

"""Utility classes and helper functions for essBATT."""

import logging
import time
import threading


_logger = logging.getLogger(__name__)


# Class from MestreLion: https://stackoverflow.com/questions/474528/how-to-repeatedly-execute-a-function-every-x-seconds
class RepeatedTimer(object):
    """Repeatedly executes a function every X seconds using threading.Timer.

    If a previous invocation is still running when the next tick fires, that
    tick is skipped (no overlapping control/monitor cycles).
    """

    def __init__(self, interval, function, *args, **kwargs):
        self._timer = None
        self._run_lock = threading.Lock()
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.is_running = False
        self.overlap_skips = 0
        self.next_call = time.time()
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        if not self._run_lock.acquire(blocking=False):
            self.overlap_skips += 1
            _logger.warning(
                'RepeatedTimer skipped overlapping call of '
                + getattr(self.function, '__name__', str(self.function))
            )
            return
        try:
            self.function(*self.args, **self.kwargs)
        finally:
            self._run_lock.release()

    def start(self):
        if not self.is_running:
            now = time.time()
            if self.next_call < now:
                self.next_call = now
            self.next_call += self.interval
            delay = max(0.01, self.next_call - time.time())
            self._timer = threading.Timer(delay, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        if self._timer:
            self._timer.cancel()
        self.is_running = False
