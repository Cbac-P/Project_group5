"""URL Frontier: hàng đợi FIFO (BFS) và tập URL đã thấy để không xếp một URL hai lần."""

from collections import deque


class Frontier:
    def __init__(self):
        self.queue = deque()   # (url, depth)
        self.seen = set()

    def add(self, url, depth):
        """Trả về False nếu URL đã từng được thêm."""
        if url in self.seen:
            return False
        self.seen.add(url)
        self.queue.append((url, depth))
        return True

    def pop(self):
        return self.queue.popleft()

    def __len__(self):
        return len(self.queue)
