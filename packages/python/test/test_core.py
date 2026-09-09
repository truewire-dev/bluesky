"""The hand-written core, without the network: hosts, the query it sends, errors.

Nothing here reaches bsky.app or bsky.network: the HTTP calls go through an in-memory
`httpx` transport that records the request the core built, and the Jetstream side is
checked at the URL its subscription would open.
"""

import httpx
import pytest
from truewire_core.exceptions import ApiError, AuthError, BadRequest, RateLimited

from bluesky import Bluesky
from bluesky.core import BSKY_SOCIAL, JETSTREAM, PUBLIC_APPVIEW, raise_for_status
from bluesky.core.ws import subscribe_url


def test_no_credentials_means_the_public_appview():
  client = Bluesky.new()
  assert client.client.base_url == PUBLIC_APPVIEW
  assert client.client.access_jwt is None
  assert client.socket.url == JETSTREAM


def test_a_jwt_moves_the_xrpc_host_to_bsky_social():
  client = Bluesky.new(access_jwt='TEST_JWT')
  assert client.client.base_url == BSKY_SOCIAL
  assert client.client.headers(public=True)['Authorization'] == 'Bearer TEST_JWT'


def test_a_base_url_wins_over_both():
  client = Bluesky.new(base_url='http://127.0.0.1:1', access_jwt='TEST_JWT')
  assert client.client.base_url == 'http://127.0.0.1:1'


def test_a_non_public_endpoint_with_no_credential_is_refused_before_the_request():
  """A client built for the public AppView cannot reach the write half, and says so
  naming both ways to fix it rather than only the one it used to know about."""
  client = Bluesky.new()
  with pytest.raises(AuthError, match='needs a session'):
    client.client.headers(public=False)


def test_an_app_password_satisfies_a_non_public_endpoint():
  """The transport can authenticate once it holds credentials, before any session exists:
  `can_authenticate` is what the refusal above is really testing."""
  client = Bluesky.new(identifier='example.invalid', app_password='not-a-real-app-password')
  assert client.client.can_authenticate


def test_an_app_password_sends_writes_to_the_pds_not_the_appview():
  """A write against `public.api.bsky.app` would 401 forever. The host follows from the
  credential, so a caller who passes one never has to know that."""
  assert Bluesky.new().client.base_url == PUBLIC_APPVIEW
  assert (
    Bluesky.new(
      identifier='example.invalid', app_password='not-a-real-app-password'
    ).client.base_url
    == BSKY_SOCIAL
  )


def test_creating_a_session_needs_the_credentials_it_injects():
  """`inject: password` with nothing to inject fails at the transport, not on the wire."""
  client = Bluesky.new()
  with pytest.raises(AuthError, match='identifier='):
    client.client.injected('password')


def test_refreshing_needs_a_session_to_refresh():
  client = Bluesky.new(identifier='example.invalid', app_password='not-a-real-app-password')
  with pytest.raises(AuthError, match='no session to refresh'):
    client.client.injected('refresh')


def test_an_expired_token_is_an_auth_failure_even_though_it_arrives_as_a_400():
  """Bluesky answers an expired token with HTTP 400 and an `ExpiredToken` code, not a 401.
  Mapping by status alone would call it a bad request, and the transport's
  refresh-and-retry -- which is keyed on `AuthError` -- would never fire."""
  with pytest.raises(AuthError, match='ExpiredToken'):
    raise_for_status(
      'POST',
      '/xrpc/com.atproto.repo.createRecord',
      400,
      '{"error": "ExpiredToken", "message": "Token has expired"}',
    )


def test_an_ordinary_400_is_still_a_bad_request():
  """A guard on the guard above: the code is consulted, not ignored."""
  with pytest.raises(BadRequest):
    raise_for_status(
      'GET',
      '/xrpc/app.bsky.actor.getProfile',
      400,
      '{"error": "InvalidRequest", "message": "Error: actor must be a valid did or handle"}',
    )


def capturing(client: Bluesky, seen: list[httpx.Request]) -> None:
  """Route the transport's HTTP through an in-memory handler that records the request."""

  def handler(request: httpx.Request) -> httpx.Response:
    seen.append(request)
    return httpx.Response(200, json={'did': 'did:plc:test'})

  client.client.http._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_query_endpoint_puts_everything_in_the_query_string():
  seen: list[httpx.Request] = []
  client = Bluesky.new()
  capturing(client, seen)
  async with client:
    await client.feed.search_posts(q='atproto', author='bsky.app', limit=3, validate=False)
  [request] = seen
  assert request.method == 'GET'
  assert request.url.host == 'public.api.bsky.app'
  assert request.url.path == '/xrpc/app.bsky.feed.searchPosts'
  query = request.url.params
  assert query['q'] == 'atproto'
  assert query['author'] == 'bsky.app'
  assert query['limit'] == '3'
  assert 'cursor' not in query, 'an omitted parameter is not sent'
  assert 'Authorization' not in request.headers


@pytest.mark.asyncio
async def test_a_list_parameter_travels_as_repeated_keys():
  seen: list[httpx.Request] = []
  client = Bluesky.new(access_jwt='TEST_JWT')
  capturing(client, seen)
  async with client:
    await client.actor.get_profiles(actors=['bsky.app', 'atproto.com'], validate=False)
  [request] = seen
  assert request.url.host == 'bsky.social'
  assert request.url.params.get_list('actors') == ['bsky.app', 'atproto.com']
  assert request.headers['Authorization'] == 'Bearer TEST_JWT'


def test_errors_map_onto_the_runtime_exceptions():
  xrpc = '{"error": "InvalidRequest", "message": "Error: actor must be a valid did or handle"}'
  with pytest.raises(BadRequest, match='InvalidRequest: Error: actor must be'):
    raise_for_status('GET', '/xrpc/app.bsky.actor.getProfile', 400, xrpc)
  with pytest.raises(AuthError):
    raise_for_status('GET', '/xrpc/app.bsky.actor.getProfile', 401, '{"error": "AuthMissing"}')
  with pytest.raises(RateLimited):
    raise_for_status('GET', '/xrpc/app.bsky.actor.getProfile', 429, 'Rate Limit Exceeded')
  with pytest.raises(ApiError, match='HTTP 502: <html>'):
    raise_for_status('GET', '/xrpc/app.bsky.actor.getProfile', 502, '<html>bad gateway</html>')


def test_a_subscription_carries_its_parameters_in_the_connection_url():
  """Jetstream has no subscribe frame; the filter is the query string of the socket."""
  url = subscribe_url(
    JETSTREAM,
    {'wantedCollections': ['app.bsky.feed.post', 'app.bsky.feed.like'], 'cursor': None},
  )
  assert url == (
    'wss://jetstream2.us-east.bsky.network/subscribe'
    '?wantedCollections=app.bsky.feed.post&wantedCollections=app.bsky.feed.like'
  )
  assert subscribe_url(JETSTREAM, {}) == JETSTREAM
