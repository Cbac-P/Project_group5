"""Breadth-First Search URL frontier."""
from collections import deque
from typing import Deque, Optional, Tuple


class URLFrontier:
    """FIFO frontier used to implement BFS.

    The frontier keeps two sets:
    - queued: URLs waiting to be crawled
    - visited: URLs already processed/attempted

    This prevents the same URL from being placed in the queue repeatedly.
    """

    def __init__(self) -> None:
        self._queue: Deque[Tuple[str, int]] = deque()
        self.queued: set[str] = set()
        self.visited: set[str] = set()

    def add(self, url: str, depth: int) -> bool:
        if url in self.visited or url in self.queued:
            return False
        self._queue.append((url, depth))
        self.queued.add(url)
        return True

    def pop(self) -> Optional[Tuple[str, int]]:
        if not self._queue:
            return None
        url, depth = self._queue.popleft()
        self.queued.discard(url)
        return url, depth

    def mark_visited(self, url: str) -> None:
        self.visited.add(url)
        self.queued.discard(url)

    def __len__(self) -> int:
        return len(self._queue)

    def __bool__(self) -> bool:
        return bool(self._queue)

    def snapshot(self, limit: int = 20) -> list[Tuple[str, int]]:
        """Return a small, non-destructive view of the waiting queue."""
        return list(self._queue)[:limit]
