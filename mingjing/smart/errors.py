class TaskCancelled(InterruptedError):
    """Cancellation crosses batch, host preparation, and native runtime boundaries."""


class ReplayMismatch(RuntimeError):
    """A frozen input or a required cached response differs from its lock."""

