"""Everything a job needs from the outside world, injected so tests can replace it."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Runtime:
    state_dir: Path
    client: object          # BinanceClient-compatible: request(), sync_clock()
    clock: object           # () -> epoch seconds
    sleep: object           # (seconds) -> None
    send: object            # (text) -> bool, private Telegram
    release_commit: str = 'unknown'
    store: object = field(default=None)

    def __post_init__(self):
        from .store import Store
        self.state_dir = Path(self.state_dir)
        if self.store is None:
            self.store = Store(self.state_dir)
