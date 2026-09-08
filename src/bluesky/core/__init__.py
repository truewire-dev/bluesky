"""Hand-written core for the Bluesky client: the AppView's HTTP transport, the XRPC
error mapping, and the base classes every generated endpoint subclasses.

Nothing here is generated, and regenerating the client never touches it. Generated `rpc`
endpoints subclass `Endpoint` and call `self.request(...)`; the generated Jetstream
endpoint subclasses `StreamEndpoint` and calls `self.subscribe(...)`, which opens one
WebSocket per subscription through `.ws.SocketClient`.

Every endpoint in this client is public: the AppView at `public.api.bsky.app` answers
them without credentials. An access JWT (from `com.atproto.server.createSession`, which
this client does not wrap) is accepted for calls against `bsky.social`, where the same
endpoints also carry the viewer's own state, but nothing here needs one.
"""

import json
from dataclasses import dataclass, field
from types import UnionType

from typing_extensions import Any, Self, TypeVar, cast

from truewire_core.exceptions import ApiError, AuthError, BadRequest, RateLimited
from truewire_core.http import HttpClient
from truewire_core.util import StreamManager
from truewire_core.validation import validator

from ..meta import DefaultMeta as Meta
from .ws import SocketClient

T = TypeVar('T')

PUBLIC_APPVIEW = 'https://public.api.bsky.app'
"""The public AppView: every endpoint here, no credentials, rate-limited by IP."""

BSKY_SOCIAL = 'https://bsky.social'
"""Bluesky's own PDS entryway: the same endpoints with an access JWT, plus the
authenticated ones this client does not cover."""

JETSTREAM = 'wss://jetstream2.us-east.bsky.network/subscribe'
"""One of the public Jetstream instances. The others (`jetstream1.us-east`,
`jetstream1.us-west`, `jetstream2.us-west`) serve the same stream."""


def raise_for_status(method: str, path: str, status: int, text: str) -> None:
  """Map a non-2xx XRPC answer onto the runtime's exceptions.

  An XRPC error body is `{"error": "InvalidRequest", "message": "..."}`; both parts
  are useful, so both go into the message. `400` is a bad request, `401`/`403` an auth
  failure, `429` the rate limit; everything else is an `ApiError`.
  """
  reason = text[:200]
  try:
    body = json.loads(text)
  except ValueError:
    body = None
  if isinstance(body, dict) and isinstance(body.get('error'), str):
    reason = body['error']
    if isinstance(body.get('message'), str):
      reason = f'{reason}: {body["message"]}'
  message = f'{method} {path}: HTTP {status}: {reason}'
  if status == 400:
    raise BadRequest(message)
  if status in (401, 403):
    raise AuthError(message)
  if status == 429:
    raise RateLimited(message)
  raise ApiError(message)


def render(request: Any, request_type: type[Any] | UnionType | None) -> dict[str, Any]:
  """The request's fields in wire form, without the ones left unset.

  Rendering through the request's own type applies every declared wire format: a
  Jetstream `cursor` passed as a `datetime` becomes Unix microseconds, never `str()` of
  a `datetime`.
  """
  if request is None:
    return {}
  if request_type is None:
    rendered = dict(request)
  else:
    rendered = json.loads(validator(cast(type, request_type)).dump(request))
  return {k: v for k, v in rendered.items() if v is not None}


@dataclass(kw_only=True)
class Transport:
  """The HTTP transport: one host, one connection pool, an optional access JWT."""

  base_url: str
  http: HttpClient = field(default_factory=HttpClient)
  access_jwt: str | None = None
  validate: bool = True

  def headers(self, *, public: bool) -> dict[str, str]:
    """Headers for one call.

    The JWT travels whenever the client has one, public endpoint or not: on `bsky.social`
    it is what fills the viewer state on a profile or a post. A non-public call without
    one is refused here, before any request is made.
    """
    headers = {'Accept': 'application/json'}
    if self.access_jwt is not None:
      headers['Authorization'] = f'Bearer {self.access_jwt}'
    elif not public:
      raise AuthError('this endpoint needs an access JWT; pass access_jwt= to Bluesky.new()')
    return headers

  async def send(
    self, method: str, path: str, *, params: dict[str, Any], body: bytes | None, public: bool
  ) -> bytes:
    """Send one request and return the body; a non-2xx status raises.

    XRPC query endpoints take every parameter in the query string, and a list-valued one
    (`actors`, `uris`) as repeated keys, which is how `httpx` renders a list and what the
    mock server matches. A `{name}` placeholder in the path, should an endpoint ever
    declare one, is filled from the request first.
    """
    filled = path
    for name, value in list(params.items()):
      if f'{{{name}}}' in filled:
        filled = filled.replace(f'{{{name}}}', str(value))
        params.pop(name)
    response = await self.http.request(
      method,
      self.base_url.rstrip('/') + '/' + filled.lstrip('/'),
      params=params or None,
      content=body,
      headers=self.headers(public=public),
    )
    if response.status_code >= 400:
      raise_for_status(method, filled, response.status_code, response.text)
    return response.content


@dataclass(kw_only=True)
class ClientBase:
  """Root client: the HTTP transport every XRPC group shares, and the Jetstream socket."""

  client: Transport
  socket: SocketClient

  @classmethod
  def new(
    cls,
    *,
    base_url: str | None = None,
    ws_url: str = JETSTREAM,
    access_jwt: str | None = None,
    validate: bool = True,
  ) -> Self:
    """Create a client.

    Args:
      base_url: The XRPC host. Omit it for the public AppView, or for `bsky.social`
        when `access_jwt` is given. A `truewire mock` address in tests.
      ws_url: The Jetstream instance to subscribe to. Each subscription opens its own
        connection to it, with the subscription's parameters in the URL.
      access_jwt: An access JWT from `com.atproto.server.createSession`, sent as a
        bearer token on every call. None of the endpoints here needs one.
      validate: Whether responses and pushed events are validated against their
        declared types by default. A call's own `validate=` overrides it.
    """
    host = base_url if base_url is not None else BSKY_SOCIAL if access_jwt else PUBLIC_APPVIEW
    return cls(
      client=Transport(base_url=host, access_jwt=access_jwt, validate=validate),
      socket=SocketClient.new(ws_url, validate=validate),
    )

  async def __aenter__(self) -> Self:
    return self

  async def __aexit__(self, exc_type, exc_value, traceback):
    await self.socket.__aexit__(exc_type, exc_value, traceback)
    await self.client.http.__aexit__(exc_type, exc_value, traceback)


@dataclass(kw_only=True, frozen=True)
class Endpoint:
  """Base for every generated XRPC endpoint class: the shared HTTP transport."""

  client: Transport

  async def request(
    self,
    request: Any = None,
    *,
    method: str,
    path: str,
    validate: bool | None = None,
    request_type: type[Any] | UnionType | None = None,
    response_type: type[T] | UnionType | None = None,
    meta: Meta = {},
  ) -> T:
    """Send one request and validate the reply against `response_type`.

    Every endpoint here is a `GET` with query parameters. A `POST`/`PUT`/`PATCH`, should
    one be added, sends the rendered request as a JSON body instead.
    """
    params = render(request, request_type)
    body = None
    if method.upper() in ('POST', 'PUT', 'PATCH') and params:
      body = json.dumps(params).encode()
      params = {}
    raw = await self.client.send(
      method, path, params=params, body=body, public=bool(meta.get('public'))
    )
    if response_type is None:
      return None  # type: ignore[return-value]
    check = self.client.validate if validate is None else validate
    if check:
      return validator(cast(type, response_type)).json(raw)
    return json.loads(raw)


@dataclass(kw_only=True, frozen=True)
class StreamEndpoint:
  """Base for the generated Jetstream endpoint class: the socket client."""

  client: SocketClient

  def subscribe(
    self,
    channel: str,
    parameters: Any = None,
    *,
    validate: bool | None = None,
    request_type: type[Any] | UnionType | None = None,
    response_type: type[T] | UnionType | None = None,
  ) -> StreamManager[T, Any, Any]:
    """Subscribe with `parameters`; each pushed event validates against `response_type`.

    Jetstream has no subscribe frame: the parameters become the connection URL's query
    string, and `channel` only names the subscription locally.
    """
    params = render(parameters, request_type)
    payload_validator = validator(cast(type, response_type)) if response_type is not None else None
    return self.client.subscribe(
      channel, params, payload_validator=payload_validator, validate=validate
    )
