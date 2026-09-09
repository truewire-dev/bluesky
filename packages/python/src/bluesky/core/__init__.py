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
  # An expired or invalid token comes back as HTTP 400 with an `ExpiredToken` /
  # `InvalidToken` code, not as a 401. Mapping it by status alone would call it a bad
  # request, which is both wrong for a caller reading the exception and wrong for the
  # transport, whose refresh-and-retry is keyed on `AuthError`. The code is the authority
  # here, not the status.
  code = body.get('error') if isinstance(body, dict) else None
  if code in ('ExpiredToken', 'InvalidToken', 'AuthMissing', 'AuthenticationRequired'):
    raise AuthError(message)
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
class Session:
  """A live session: the two tokens, and who they belong to.

  Held in memory on the transport and never written anywhere. The access token is
  short-lived by design, so this is expected to be replaced several times over the life of
  a long-running client.
  """

  access_jwt: str
  refresh_jwt: str
  did: str
  handle: str


@dataclass(kw_only=True)
class Transport:
  """The HTTP transport: one host, one connection pool, and at most one session.

  Three ways to authenticate, in the order the transport prefers them:

  1. **An app password.** `identifier` plus `app_password`, exchanged for a session on the
     first authenticated call and refreshed when it expires. This is what a caller wants.
  2. **An access JWT the caller already has.** Passed straight through, never refreshed --
     the transport has no refresh token to refresh it with, so an expired one raises rather
     than silently failing.
  3. **Nothing at all.** Every read endpoint here is public and needs none.

  The credentials are the transport's, not any call's: they are injected into the body of
  `server.create_session` at send time, which is why no endpoint's request schema names
  them and no recorded example can contain one.
  """

  base_url: str
  http: HttpClient = field(default_factory=HttpClient)
  access_jwt: str | None = None
  identifier: str | None = None
  app_password: str | None = None
  validate: bool = True
  session: Session | None = None

  @property
  def can_authenticate(self) -> bool:
    """Whether this transport can produce a bearer token for a non-public call."""
    return (
      self.access_jwt is not None
      or self.session is not None
      or (self.identifier is not None and self.app_password is not None)
    )

  def bearer(self) -> str | None:
    """The token to send, preferring a session this transport owns and can refresh."""
    if self.session is not None:
      return self.session.access_jwt
    return self.access_jwt

  def headers(self, *, public: bool, bearer: str | None = None) -> dict[str, str]:
    """Headers for one call.

    A token travels whenever the transport has one, public endpoint or not: on
    `bsky.social` it is what fills the viewer state on a profile or a post. A non-public
    call with no way to get one is refused here, before any request is made.
    """
    headers = {'Accept': 'application/json'}
    token = bearer if bearer is not None else self.bearer()
    if token is not None:
      headers['Authorization'] = f'Bearer {token}'
    elif not public:
      raise AuthError(
        'this endpoint needs a session; pass identifier= and app_password= (or an '
        'access_jwt=) to Bluesky.new()'
      )
    return headers

  def injected(self, inject: str | None) -> tuple[dict[str, Any], str | None]:
    """What the transport adds to one call that the caller never passed.

    Returns the body fields to merge in, and a bearer token to use in place of the usual
    one. `password` fills `create_session`'s two credential fields; `refresh` swaps the
    access token for the refresh token, which is what `refresh_session` authenticates with.
    """
    if inject == 'password':
      if self.identifier is None or self.app_password is None:
        raise AuthError('creating a session needs identifier= and app_password= on Bluesky.new()')
      return {'identifier': self.identifier, 'password': self.app_password}, None
    if inject == 'refresh':
      if self.session is None:
        raise AuthError('there is no session to refresh')
      return {}, self.session.refresh_jwt
    return {}, None

  async def send(
    self,
    method: str,
    path: str,
    *,
    params: dict[str, Any],
    body: bytes | None,
    public: bool,
    bearer: str | None = None,
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
    headers = self.headers(public=public, bearer=bearer)
    if body is not None:
      # XRPC refuses a body with no declared encoding (`InvalidRequest: Request encoding
      # (Content-Type) required but not provided`). The read half of this client is all
      # GETs, so this only became necessary when the write half arrived.
      headers['Content-Type'] = 'application/json'
    response = await self.http.request(
      method,
      self.base_url.rstrip('/') + '/' + filled.lstrip('/'),
      params=params or None,
      content=body,
      headers=headers,
    )
    if response.status_code >= 400:
      raise_for_status(method, filled, response.status_code, response.text)
    return response.content

  async def sign_in(self) -> Session:
    """Exchange the app password for a session, and remember it.

    Called by the transport itself before the first authenticated request. A caller can
    call it early to fail fast on bad credentials rather than on their first real call.
    """
    fields, _ = self.injected('password')
    raw = await self.send(
      'POST',
      '/xrpc/com.atproto.server.createSession',
      params={},
      body=json.dumps(fields).encode(),
      public=True,
    )
    return self.remember(json.loads(raw))

  async def refresh(self) -> Session:
    """Trade the refresh token for a new session.

    The refresh token is the bearer here, not the access token -- which is the whole reason
    `server.refresh_session` declares `inject: "refresh"`.
    """
    _, bearer = self.injected('refresh')
    raw = await self.send(
      'POST',
      '/xrpc/com.atproto.server.refreshSession',
      params={},
      body=None,
      public=True,
      bearer=bearer,
    )
    return self.remember(json.loads(raw))

  def remember(self, payload: dict[str, Any]) -> Session:
    """Store a session frame the server just sent."""
    self.session = Session(
      access_jwt=payload['accessJwt'],
      refresh_jwt=payload['refreshJwt'],
      did=payload['did'],
      handle=payload['handle'],
    )
    return self.session

  async def ensure_session(self) -> None:
    """Make sure a call has a token to send, where this transport can produce one.

    Silent when it cannot: a client with no credentials is the ordinary case for the read
    half, and every read endpoint here is public. A non-public call with nothing to send is
    refused by `headers`, with a message naming what to pass.

    A caller who supplied an `access_jwt` gets theirs used as-is; it is not this
    transport's to replace.
    """
    if self.session is not None or self.access_jwt is not None:
      return
    if self.identifier is None or self.app_password is None:
      return
    await self.sign_in()


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
    identifier: str | None = None,
    app_password: str | None = None,
    access_jwt: str | None = None,
    validate: bool = True,
  ) -> Self:
    """Create a client.

    Reads need nothing. Writes need either an app password, which the client exchanges for
    a session and refreshes by itself, or a session token you already hold.

    Args:
      base_url: The XRPC host. Omit it for the public AppView, or for `bsky.social` when
        a credential is given -- writes only work against the account's own PDS, never
        against the AppView. A `truewire mock` address in tests.
      ws_url: The Jetstream instance to subscribe to. Each subscription opens its own
        connection to it, with the subscription's parameters in the URL.
      identifier: The account's handle or DID, such as `truewire.dev`. Paired with
        `app_password`.
      app_password: An app password from bsky.app > Settings > App Passwords. Never the
        account password: an app password is individually revocable and cannot change the
        account's password or email. It is held in memory, sent only to
        `com.atproto.server.createSession`, and never written anywhere.
      access_jwt: A session token you already have, used as-is. Not refreshed, because
        this client has no refresh token to refresh it with -- prefer `app_password`.
      validate: Whether responses and pushed events are validated against their
        declared types by default. A call's own `validate=` overrides it.
    """
    credentialed = access_jwt is not None or (identifier is not None and app_password is not None)
    host = base_url if base_url is not None else BSKY_SOCIAL if credentialed else PUBLIC_APPVIEW
    return cls(
      client=Transport(
        base_url=host,
        access_jwt=access_jwt,
        identifier=identifier,
        app_password=app_password,
        validate=validate,
      ),
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

    A read is a `GET` with query parameters; a write is a `POST` carrying the rendered
    request as a JSON body. Three things happen here that the generated code does not know
    about, all of them driven by the endpoint's own `meta`:

    1. **Injection.** `meta.inject` names credentials that belong to the client rather than
       to the call, and the transport merges them into the body at send time. This is what
       keeps them out of every request schema, and therefore out of every recording.
    2. **Sessions.** A non-public endpoint gets a session created first, if the transport
       owns the credentials to create one.
    3. **One retry on an expired token.** An access token lasts minutes, so a client that
       has been idle finds a stale one on its next call. That is not an error a caller
       should have to handle: the transport refreshes and re-sends, exactly once.
    """
    inject = meta.get('inject')
    public = bool(meta.get('public'))
    # A session is created before *any* call the transport can authenticate, not only a
    # non-public one. Two reasons: on `bsky.social` a read needs one, where the same read
    # needs nothing on the public AppView -- so a client given credentials and pointed at
    # the PDS would 401 on every read otherwise -- and a session is what fills the viewer's
    # own state on a profile or a post.
    #
    # `password` is the exception: it is how a session is made, so ensuring one would
    # recurse.
    if inject != 'password':
      await self.client.ensure_session()

    fields, bearer = self.client.injected(inject)
    params = render(request, request_type)
    body = None
    if method.upper() in ('POST', 'PUT', 'PATCH'):
      merged = {**params, **fields}
      body = json.dumps(merged).encode() if merged else None
      params = {}
    elif fields:
      params = {**params, **fields}

    async def send() -> bytes:
      return await self.client.send(
        method, path, params=dict(params), body=body, public=public, bearer=bearer
      )

    try:
      raw = await send()
    except AuthError:
      # The transport can only recover a token it owns and can refresh. A caller-supplied
      # `access_jwt` is theirs, and re-sending would fail identically.
      if inject is not None or self.client.session is None:
        raise
      await self.client.refresh()
      raw = await send()

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
