"""What each recording proves about the typed result, beyond validating.

One test per example request. An example whose response has not been recorded yet skips
with the reason, so the suite is green before the first run of `test/recapture.sh` and
turns into real coverage the moment the recordings land. The assertions are structural
(row counts, identifiers, ordering): the counts and the text move with every
re-recording, the shape does not.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from truewire.examples import run_example_request
from truewire.spec.repo import endpoint_records, load_request_example
from typing_extensions import Any

PROJECT = Path(__file__).resolve().parents[1]

BSKY_APP = 'bsky.app'
ATPROTO = 'atproto.com'
HELLO = 'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.post/3juzlwllznd24'


def is_profile(profile: Any) -> None:
  """Every profile view carries the two identifiers that make an account addressable."""
  assert profile['did'].startswith('did:')
  assert isinstance(profile['handle'], str) and profile['handle']


def is_post(post: Any) -> None:
  """Every post view carries its address, its author and the record as written."""
  assert post['uri'].startswith('at://')
  assert isinstance(post['cid'], str)
  is_profile(post['author'])
  assert isinstance(post['record']['text'], str)


def profile(view: Any) -> None:
  assert view['handle'] == BSKY_APP
  assert view['did'].startswith('did:')
  assert view['followersCount'] > 0
  assert view['postsCount'] > 0


def profiles(views: Any) -> None:
  assert [view['handle'] for view in views['profiles']] == [BSKY_APP, ATPROTO]
  for view in views['profiles']:
    is_profile(view)


def actor_search(results: Any) -> None:
  assert 1 <= len(results['actors']) <= 3, 'limit=3 caps the page'
  for view in results['actors']:
    is_profile(view)


def author_feed(feed: Any) -> None:
  assert 1 <= len(feed['feed']) <= 3
  for entry in feed['feed']:
    is_post(entry['post'])
    assert 'reply' not in entry, 'filter=posts_no_replies leaves replies out'


def custom_feed(feed: Any) -> None:
  assert 1 <= len(feed['feed']) <= 3
  for entry in feed['feed']:
    is_post(entry['post'])


def post_thread(thread: Any) -> None:
  root = thread['thread']
  assert root['$type'] == 'app.bsky.feed.defs#threadViewPost'
  assert root['post']['uri'] == HELLO
  assert 'parent' not in root, 'parentHeight=0 asks for no ancestors'
  is_post(root['post'])


def posts(result: Any) -> None:
  assert len(result['posts']) == 1
  assert result['posts'][0]['uri'] == HELLO
  is_post(result['posts'][0])


def post_search(results: Any) -> None:
  assert 1 <= len(results['posts']) <= 3
  for post in results['posts']:
    is_post(post)
    assert post['author']['handle'] == BSKY_APP, 'author=bsky.app narrows the search'


def followers(page: Any) -> None:
  assert page['subject']['handle'] == ATPROTO
  assert 1 <= len(page['followers']) <= 3
  for view in page['followers']:
    is_profile(view)


def follows(page: Any) -> None:
  assert page['subject']['handle'] == BSKY_APP
  assert 1 <= len(page['follows']) <= 3
  for view in page['follows']:
    is_profile(view)


def resolved(handle: Any) -> None:
  assert handle['did'].startswith('did:')


PROVES: dict[str, Callable[[Any], None]] = {
  'actor.get_profile[bsky_app]': profile,
  'actor.get_profiles[bsky_and_atproto]': profiles,
  'actor.search_actors[bluesky_page1]': actor_search,
  'feed.get_author_feed[bsky_app_page1]': author_feed,
  'feed.get_feed[whats_hot_page1]': custom_feed,
  'feed.get_post_thread[bsky_app_hello]': post_thread,
  'feed.get_posts[bsky_app_hello]': posts,
  'feed.search_posts[atproto_latest]': post_search,
  'graph.get_followers[atproto_page1]': followers,
  'graph.get_follows[bsky_app_page1]': follows,
  'identity.resolve_handle[bsky_app]': resolved,
}


def recorded_requests() -> list[Any]:
  """Every HTTP example request in the project, recorded or not, as a pytest param."""
  params = []
  spec = PROJECT / 'spec'
  for record in endpoint_records(PROJECT):
    if 'http' not in record.endpoint.transports:
      continue
    function = record.endpoint.resolved_function(record.path, spec)
    for request in sorted((record.path.parent / 'examples').glob('*.request.json')):
      example_id = request.name.removesuffix('.request.json')
      params.append(pytest.param(record, request, id=f'{function}[{example_id}]'))
  return params


def test_every_request_has_an_assertion():
  """A new example request needs a `PROVES` entry, or its recording proves nothing here."""
  ids = {param.id for param in recorded_requests()}
  assert ids == set(PROVES)


@pytest.mark.asyncio
@pytest.mark.parametrize('record,request_file', recorded_requests())
async def test_recording(client, record, request_file, request):
  response_file = request_file.with_name(
    request_file.name.replace('.request.json', '.response.json')
  )
  if not response_file.exists():
    pytest.skip(f'{request.node.callspec.id}: no response recorded yet; run test/recapture.sh')
  assert json.loads(response_file.read_text())['status'] == 200
  async with client:
    result = await run_example_request(
      client,
      record.endpoint,
      load_request_example(request_file),
      client_root=PROJECT,
      endpoint_path=record.path,
    )
  PROVES[request.node.callspec.id](result)
