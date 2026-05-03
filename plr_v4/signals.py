"""PLR Signals — typed, subscribable channels between drivers and consumers.

A Signal is both a class-level descriptor (metadata for introspection) and a
runtime channel (per-instance emit/stream/read/subscribe).

Three kinds:
    SignalR  — readable sensor output. Emits Readings, supports streaming.
    SignalRW — readable and settable parameter. Extends SignalR with set().
    SignalX  — trigger / action. Emits Events when triggered.

Declaration (on capability class)::

    class PHReadingCapability(ABC):
        ph = SignalR(unit="pH", dtype="number", precision=3, limits=(0.0, 14.0))

Push mode (driver emits)::

    self.ph.emit(7.234)

Pull mode (consumer triggers hardware read)::

    self.volume.bind_producer(self._read_volume)
    reading = await self.volume.read()

Backpressure: bounded queue per subscriber. When full, oldest reading is dropped
with a warning. Configurable via max_queue_size parameter on Signal declaration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUEUE_SIZE = 100


@dataclass(frozen=True)
class DataKey:
    """Metadata describing what a signal produces.

    Modeled after Bluesky event-model's DataKey. Declared at class level
    and accessible BEFORE setup() — for protocol planning and introspection.
    """

    source: str = ""
    dtype: str = "number"
    shape: tuple[int, ...] = ()
    unit: str = ""
    precision: Optional[int] = None
    limits: Optional[tuple[float, float]] = None
    description: str = ""


@dataclass
class Reading:
    """A single timestamped value from a SignalR or SignalRW."""

    value: Any
    timestamp: float
    signal_name: str
    datakey: DataKey


@dataclass
class Event:
    """A timestamped trigger event from a SignalX."""

    timestamp: float
    signal_name: str
    payload: Any = None


class BoundSignal:
    """Per-instance readable signal with emit/stream/read/subscribe.

    Created automatically when a SignalR descriptor is accessed on an instance.
    """

    def __init__(self, descriptor: SignalR) -> None:
        self.descriptor = descriptor
        self._subscribers: List[asyncio.Queue[Reading]] = []
        self._callbacks: List[Callable[[Reading], Any]] = []
        self._last_reading: Optional[Reading] = None
        self._producer: Optional[Callable] = None

    @property
    def name(self) -> str:
        return self.descriptor.name

    @property
    def datakey(self) -> DataKey:
        return self.descriptor.datakey

    @property
    def last_reading(self) -> Optional[Reading]:
        return self._last_reading

    @property
    def has_subscribers(self) -> bool:
        return len(self._subscribers) > 0 or len(self._callbacks) > 0

    def bind_producer(self, producer: Callable) -> None:
        """Bind a pull-mode producer. read() calls this instead of waiting."""
        self._producer = producer

    def emit(self, value: Any, timestamp: Optional[float] = None) -> Reading:
        """Push a value to all subscribers and callbacks."""
        reading = Reading(
            value=value,
            timestamp=timestamp if timestamp is not None else time.time(),
            signal_name=self.descriptor.name,
            datakey=self.descriptor.datakey,
        )
        self._last_reading = reading

        for queue in self._subscribers:
            try:
                queue.put_nowait(reading)
            except asyncio.QueueFull:
                logger.warning("Signal %r queue full, dropped reading", self.name)

        for cb in self._callbacks:
            try:
                cb(reading)
            except Exception:
                logger.exception("Signal %r callback error", self.name)

        return reading

    async def read(self, timeout: Optional[float] = None) -> Reading:
        """Return the next reading (pull or push mode)."""
        if self._producer is not None:
            value = self._producer()
            if asyncio.iscoroutine(value):
                value = await value
            return self.emit(value)

        queue: asyncio.Queue[Reading] = asyncio.Queue(
            maxsize=self.descriptor.max_queue_size
        )
        self._subscribers.append(queue)
        try:
            if timeout is not None:
                return await asyncio.wait_for(queue.get(), timeout=timeout)
            return await queue.get()
        finally:
            self._subscribers.remove(queue)

    async def stream(self) -> AsyncIterator[Reading]:
        """Async iterator yielding readings continuously."""
        if self._producer is not None:
            while True:
                try:
                    value = self._producer()
                    if asyncio.iscoroutine(value):
                        value = await value
                except StopIteration:
                    return
                yield self.emit(value)
        else:
            queue: asyncio.Queue[Reading] = asyncio.Queue(
                maxsize=self.descriptor.max_queue_size
            )
            self._subscribers.append(queue)
            try:
                while True:
                    yield await queue.get()
            finally:
                self._subscribers.remove(queue)

    def subscribe(self, callback: Callable[[Reading], Any]) -> Callable[[], None]:
        """Register a callback. Returns an unsubscribe function."""
        self._callbacks.append(callback)

        def unsubscribe():
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe


class BoundSignalRW(BoundSignal):
    """Per-instance read-write signal. Adds set() for settable parameters."""

    def __init__(self, descriptor: SignalRW) -> None:
        super().__init__(descriptor)
        self._setter: Optional[Callable] = None

    def bind_setter(self, setter: Callable) -> None:
        self._setter = setter

    async def set(self, value: Any) -> None:
        if self._setter is None:
            raise RuntimeError(f"No setter bound for signal {self.name!r}")
        result = self._setter(value)
        if asyncio.iscoroutine(result):
            await result


class BoundSignalX:
    """Per-instance trigger signal with emit/stream/subscribe."""

    def __init__(self, descriptor: SignalX) -> None:
        self.descriptor = descriptor
        self._subscribers: List[asyncio.Queue[Event]] = []
        self._callbacks: List[Callable[[Event], Any]] = []
        self._last_event: Optional[Event] = None

    @property
    def name(self) -> str:
        return self.descriptor.name

    @property
    def last_event(self) -> Optional[Event]:
        return self._last_event

    @property
    def has_subscribers(self) -> bool:
        return len(self._subscribers) > 0 or len(self._callbacks) > 0

    def emit(self, payload: Any = None, timestamp: Optional[float] = None) -> Event:
        event = Event(
            timestamp=timestamp if timestamp is not None else time.time(),
            signal_name=self.descriptor.name,
            payload=payload,
        )
        self._last_event = event

        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Signal %r queue full, dropped event", self.name)

        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                logger.exception("Signal %r callback error", self.name)

        return event

    async def stream(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(
            maxsize=DEFAULT_MAX_QUEUE_SIZE
        )
        self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)

    def subscribe(self, callback: Callable[[Event], Any]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe():
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe


class SignalR:
    """Readable sensor output — a measurand (class-level descriptor).

    At class level: metadata for introspection (get_signals()).
    At instance level: returns a BoundSignal with emit/stream/read/subscribe.
    """

    def __init__(
        self,
        *,
        unit: str = "",
        dtype: str = "number",
        shape: tuple[int, ...] = (),
        precision: Optional[int] = None,
        limits: Optional[tuple[float, float]] = None,
        description: str = "",
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        self.datakey = DataKey(
            unit=unit, dtype=dtype, shape=shape,
            precision=precision, limits=limits, description=description,
        )
        self.max_queue_size = max_queue_size
        self.name: str = ""
        self._attr: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        self._attr = f"_signal_{name}"

    def __get__(self, obj: Any, objtype: type = None) -> Union[SignalR, BoundSignal]:
        if obj is None:
            return self
        if not hasattr(obj, self._attr):
            setattr(obj, self._attr, self._make_bound())
        return getattr(obj, self._attr)

    def _make_bound(self) -> BoundSignal:
        return BoundSignal(self)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name!r}, unit={self.datakey.unit!r}, "
            f"dtype={self.datakey.dtype!r})"
        )


class SignalRW(SignalR):
    """Readable + settable parameter."""

    def _make_bound(self) -> BoundSignalRW:
        return BoundSignalRW(self)


class SignalX:
    """Trigger or action — no continuous value (class-level descriptor)."""

    def __init__(self, *, description: str = "", max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE) -> None:
        self.description = description
        self.max_queue_size = max_queue_size
        self.name: str = ""
        self._attr: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        self._attr = f"_signal_{name}"

    def __get__(self, obj: Any, objtype: type = None) -> Union[SignalX, BoundSignalX]:
        if obj is None:
            return self
        if not hasattr(obj, self._attr):
            setattr(obj, self._attr, BoundSignalX(self))
        return getattr(obj, self._attr)

    def __repr__(self) -> str:
        return f"SignalX(name={self.name!r})"


AnySignal = Union[SignalR, SignalX]


def get_signals(cls: type) -> Dict[str, AnySignal]:
    """Return all Signal declarations from a class and its MRO."""
    signals: Dict[str, AnySignal] = {}
    for klass in reversed(cls.__mro__):
        for name, val in vars(klass).items():
            if isinstance(val, (SignalR, SignalX)):
                signals[name] = val
    return signals


__all__ = [
    "AnySignal", "BoundSignal", "BoundSignalRW", "BoundSignalX",
    "DataKey", "Event", "Reading",
    "SignalR", "SignalRW", "SignalX",
    "get_signals", "DEFAULT_MAX_QUEUE_SIZE",
]
