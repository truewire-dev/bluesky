"""The WebSocket side of the Bluesky core: one Jetstream connection per subscription.

Jetstream is not a multiplexed subscribe/unsubscribe socket. A consumer connects to
`/subscribe?wantedCollections=...&wantedDids=...&cursor=...` and the server pushes JSON
events from the moment the socket is open until it is closed; no frame ever goes the other
way. So `Connection` is a `truewire_core.ws.Streams` whose subscribe request sends
nothing, whose unsubscribe request sends nothing, and which routes every incoming frame
to the one subscription it carries, and `SocketClient` opens a fresh `Connection` for each
subscription with that subscription's parameters in the URL.
"""

import json
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlencode

import websockets
from typing_extensions import Any, Mapping, TypeVar, cast

from truewire_core.util import Stream, StreamManager
from truewire_core.validation import validator
from truewire_core.ws import Streams
from truewire_core.ws.streams import Subscription

T = TypeVar('T')

Frame = dict[str, Any]
"""One decoded JSON event."""

CHANNEL = 'subscribe'
"""The local name of the one subscription a connection carries."""


def subscribe_url(base: str, params: Mapping[str, Any]) -> str:
  """The connection URL for one subscription: `base` plus the parameters as its query.

  A list-valued parameter (`wantedCollections`, `wantedDids`) becomes repeated keys, the
  form Jetstream documents.
  """
  pairs: list[tuple[str, str]] = []
  for name, value in params.items():
    if value is None:
      continue
    for item in value if isinstance(value, list) else [value]:
      pairs.append((name, str(item)))
  if not pairs:
    return base
  return base + ('&' if '?' in base else '?') + urlencode(pairs)


@dataclass
class Connection(Streams[Frame, Mapping[str, Any], None, None]):
  """One Jetstream socket: nothing sent, every JSON frame delivered to the subscription."""

  async def ping(self, ws: websockets.ClientConnection):
    """A protocol-level ping every `ping_interval`, so an idle filter keeps the socket alive."""
    await ws.ping()

  async def request_subscription(
    self, channel: str, params: Mapping[str, Any] | None = None
  ) -> None:
    """Nothing to send: opening the connection, which `Streams.subscribe` did to get here, is the subscription."""
    return None

  async def request_unsubscription(
    self, channel: str, params: Mapping[str, Any] | None = None
  ) -> None:
    """Nothing to send: `SocketClient` closes the socket once the stream is unsubscribed."""
    return None

  def parse_msg(self, msg: str | bytes) -> Subscription[Frame] | None:
    frame = json.loads(msg)
    if not isinstance(frame, dict):
      return None
    return {'channel': CHANNEL, 'notification': frame}


@dataclass(kw_only=True)
class SocketClient:
  """Opens one `Connection` per subscription and owns every one still open."""

  url: str
  """The Jetstream `/subscribe` URL without a query string."""
  validate: bool = True
  timeout: timedelta = timedelta(seconds=10)
  ping_interval: timedelta = timedelta(seconds=30)
  connections: list[Connection] = field(default_factory=list, init=False, repr=False)

  @classmethod
  def new(
    cls,
    url: str,
    *,
    validate: bool = True,
    timeout: timedelta = timedelta(seconds=10),
    ping_interval: timedelta = timedelta(seconds=30),
  ) -> 'SocketClient':
    """Build a client for `url`; a socket opens on each subscription."""
    return cls(url=url, validate=validate, timeout=timeout, ping_interval=ping_interval)

  async def __aenter__(self):
    return self

  async def __aexit__(self, exc_type, exc_value, traceback):
    for conn in list(self.connections):
      await conn.__aexit__(exc_type, exc_value, traceback)
    self.connections.clear()

  def subscribe(
    self,
    channel: str,
    params: Mapping[str, Any] | None = None,
    *,
    payload_validator: validator[T] | None = None,
    validate: bool | None = None,
  ) -> StreamManager[T, Any, Any]:
    """Subscribe with `params` in the URL, validating each event with `payload_validator` unless disabled.

    `channel` is Jetstream's one endpoint name, `subscribe`; it is kept as the local
    subscription key. Unsubscribing closes the socket, since a connection is a subscription.
    """
    conn = Connection(
      subscribe_url(self.url, params or {}), timeout=self.timeout, ping_interval=self.ping_interval
    )

    async def connect() -> Stream[Frame, None, None]:
      self.connections.append(conn)
      stream = await conn.subscribe(CHANNEL, params)
      inner = stream.unsubscribe

      async def unsubscribe() -> None:
        await inner()
        await conn.__aexit__(None, None, None)
        if conn in self.connections:
          self.connections.remove(conn)

      return Stream(stream.reply, stream.stream, unsubscribe)

    manager: StreamManager[Frame, None, None] = StreamManager(connect)
    check = self.validate if validate is None else validate
    if payload_validator is None or not check:
      return cast(StreamManager[T, Any, Any], manager)
    return manager.map(payload_validator.python)
