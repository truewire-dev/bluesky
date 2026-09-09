#!/usr/bin/env python3
"""Point the two post-pinned examples at a post that still exists.

`feed.get_posts` and `feed.get_post_thread` both name one post by AT URI, and a post can
be deleted. The two endpoints fail differently when that happens, and only one of them
fails loudly: `getPostThread` answers HTTP 400 `NotFound`, while `getPosts` answers HTTP
200 with an empty `posts` array, which records perfectly well and proves nothing. So
liveness is checked here, before anything is captured, rather than inferred from a
capture that failed.

When the pinned post is gone the replacement comes from the same account, and has to
have replies: a thread with none records a shape that never exercises `depth`. Both
examples are rewritten to the new URI so the pair stays consistent, and the recordings
they gain are committed beside them.

An ordinary run finds the post alive, changes nothing, and the diff stays quiet.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from bluesky import Bluesky

PROJECT = Path(__file__).resolve().parents[3]
EXAMPLES = (
  (
    PROJECT / 'spec/endpoints/feed/get_posts/examples/bsky_app_hello.request.json',
    'uris',
    'A post by {actor} with replies, fetched by URI',
  ),
  (
    PROJECT / 'spec/endpoints/feed/get_post_thread/examples/bsky_app_hello.request.json',
    'uri',
    'A post by {actor} with replies, two levels deep',
  ),
)
"""Each example that pins a post: the file, the request field the URI sits in, and what
the example is for, since repointing it makes the old description wrong."""

ACTOR = 'bsky.app'


def pinned() -> str:
  """The URI the examples name, which they have to agree on."""
  uris = set()
  for path, field, _ in EXAMPLES:
    value = json.loads(path.read_text())['request'][field]
    uris.add(value[0] if field == 'uris' else value)
  if len(uris) != 1:
    raise SystemExit(f'the examples pin different posts: {sorted(uris)}')
  return uris.pop()


async def resolve(uri: str) -> tuple[str, int] | None:
  """The pinned post if it is still there, else the most-replied post by `ACTOR`.

  `None` when the pinned post needs no replacing. The replacement is the one with the
  most replies rather than the newest, because a reply count near zero on a post an hour
  old would make the thread recording as thin as no recording at all.
  """
  async with Bluesky.new() as client:
    posts = (await client.feed.get_posts(uris=[uri]))['posts']
    if len(posts) == 1 and (posts[0].get('replyCount') or 0) > 0:
      return None
    feed = await client.feed.get_author_feed(actor=ACTOR, limit=50, filter='posts_no_replies')
  best: tuple[str, int] | None = None
  for entry in feed['feed']:
    post = entry['post']
    replies = post.get('replyCount') or 0
    if replies and (best is None or replies > best[1]):
      best = (post['uri'], replies)
  if best is None:
    raise SystemExit(f'no post by {ACTOR} currently has replies; leaving the examples alone')
  return best


def repoint(uri: str) -> None:
  """Write the new URI into both examples, with a description that still describes them."""
  for path, field, description in EXAMPLES:
    example = json.loads(path.read_text())
    example['request'][field] = [uri] if field == 'uris' else uri
    example['description'] = description.format(actor=ACTOR)
    path.write_text(json.dumps(example, indent=2) + '\n')


def main() -> None:
  was = pinned()
  found = asyncio.run(resolve(was))
  if found is None:
    print(f'the pinned post is live and has replies: {was}')
    return
  uri, replies = found
  repoint(uri)
  print(f'{was}\n  is gone or has lost its replies; now {uri} ({replies} replies)', file=sys.stderr)


if __name__ == '__main__':
  main()
