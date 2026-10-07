"""A per-user limit on how fast messages are answered.

What it protects
----------------
Not the owner from themselves - the allowlist already means one person uses
this. It protects the *budget*: fifty model requests a day on the free tier,
and an agent turn spends two. A workflow stuck retrying, a script hammering
the endpoint with a leaked key, or a phone resending a queue of messages
after a dead zone could burn the day's quota in minutes and leave the bot
mute until midnight UTC. THREAT_MODEL.md T6.

How
---
A sliding window: the timestamps of each user's recent messages, oldest
dropped as they age out. Twenty messages in ten minutes is far above how
fast a person types questions and far below what drains the quota.

In memory, in this process. A restart forgets the window, and two API
processes would each keep their own. Both are acceptable for one user on
one machine; Phase 15's deployment is where a shared store would earn its
place.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self,
        max_events: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._clock = clock
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, key: int) -> bool:
        """Record an event for `key` and say whether it is within the limit.

        A refused event is not recorded, so a user who keeps trying while
        limited is let back in as soon as the window allows - not pushed
        further back by every attempt.
        """
        now = self._clock()
        events = self._events[key]
        while events and now - events[0] >= self.window_s:
            events.popleft()
        if len(events) >= self.max_events:
            return False
        events.append(now)
        return True

    def retry_after(self, key: int) -> float:
        """Seconds until `key` may send again; 0 if it may now."""
        events = self._events.get(key)
        if not events or len(events) < self.max_events:
            return 0.0
        return max(0.0, self.window_s - (self._clock() - events[0]))
