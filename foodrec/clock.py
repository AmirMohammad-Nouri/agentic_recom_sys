"""The app's notion of "now". Normally the wall clock; scenario runs move it forward
(`{"action": "advance_time", "days": 2}`) to test what happens over days: repetition and
fatigue fading, "only today" answers expiring, cool-downs. Timeouts, sessions and
measurements keep using the real clock.
"""
import time

_offset = 0.0


def now():
    return time.time() + _offset


def advance(seconds):
    global _offset
    _offset += float(seconds)
    return now()


def reset():
    global _offset
    _offset = 0.0


def offset():
    return _offset
