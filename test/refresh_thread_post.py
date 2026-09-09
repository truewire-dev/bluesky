#!/usr/bin/env python3
"""Point the post-thread example at a post that still exists.

A thread example has to name one post, and a post can be deleted: the first recording
run failed because `at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.post/3juzlwllznd24`
had gone, and the endpoint would have stayed unverified for as long as that URI sat in
the example. So the request half is refreshed here, from the same account, choosing a
post that currently has replies (a thread with no replies would record a shape that
never exercises `depth`).

This runs only when `test/recapture.sh` could not record the example as written, so an
ordinary run leaves the committed request untouched and the diff stays quiet. When it
does run, the new URI is committed alongside the response it recorded, which is what
keeps the pair reproducible: the request half is still the source of truth, it has just
been repaired.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from bluesky import Bluesky

EXAMPLE = Path(__file__).resolve().parent.parent / (
  'spec/endpoints/feed/get_post_thread/examples/bsky_app_hello.request.json'
)
ACTOR = 'bsky.app'


async def live_thread_uri() -> tuple[str, int]:
  """The most recent post by `ACTOR` that has replies, and how many it has."""
  async with Bluesky.new() as client:
    feed = await client.feed.get_author_feed(actor=ACTOR, limit=50, filter='posts_no_replies')
  best: tuple[str, int] | None = None
  for entry in feed['feed']:
    post = entry['post']
    replies = post.get('replyCount') or 0
    if replies and (best is None or replies > best[1]):
      best = (post['uri'], replies)
  if best is None:
    raise SystemExit(f'no post by {ACTOR} currently has replies; leaving the example alone')
  return best


def main() -> None:
  uri, replies = asyncio.run(live_thread_uri())
  example = json.loads(EXAMPLE.read_text())
  if example['request']['uri'] == uri:
    print(f'{EXAMPLE.name}: already points at a live post')
    return
  was = example['request']['uri']
  example['request']['uri'] = uri
  example['description'] = f'A post by {ACTOR} with replies, two levels deep'
  EXAMPLE.write_text(json.dumps(example, indent=2) + '\n')
  print(f'{EXAMPLE.name}: {was}\n  is gone; now {uri} ({replies} replies)', file=sys.stderr)


if __name__ == '__main__':
  main()
