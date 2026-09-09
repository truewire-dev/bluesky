"""Real, representative usage of the client's public surface, type-checked by pyright
(`pyrightconfig.json`) and never executed: the guardrail against a public return type
silently degrading, and the proof that `validate=False` is typed as what it returns.

`reveal_type(..., expected_text=...)` is pyright's own assertion: a mismatch is an error.
"""

from typing_extensions import reveal_type

from bluesky import Bluesky


async def validated_by_default(strict: bool) -> None:
  """The parsed record, by default and with a flag decided elsewhere."""
  async with Bluesky.new() as client:
    profile = await client.actor.get_profile('bsky.app')
    reveal_type(profile, expected_text='ProfileViewDetailed')
    reveal_type(profile['did'], expected_text='str')
    reveal_type(profile.get('followersCount'), expected_text='int | None')
    reveal_type(profile.get('labels'), expected_text='list[Label] | None')
    reveal_type(
      await client.actor.get_profile('bsky.app', validate=strict),
      expected_text='ProfileViewDetailed',
    )
    reveal_type(
      await client.actor.get_profiles(actors=['bsky.app', 'atproto.com']),
      expected_text='Profiles',
    )
    reveal_type(await client.identity.resolve_handle('bsky.app'), expected_text='ResolvedHandle')
    posts = await client.feed.get_posts(uris=['at://did:plc:example/app.bsky.feed.post/1'])
    reveal_type(posts['posts'][0]['record']['text'], expected_text='str')
    reveal_type(posts['posts'][0]['indexedAt'], expected_text='datetime')
    thread = await client.feed.get_post_thread(
      'at://did:plc:example/app.bsky.feed.post/1', depth=2, parent_height=0
    )
    # The root of a thread is one of three shapes, so a caller narrows before reading it.
    reveal_type(thread['thread'], expected_text='ThreadViewPost | NotFoundPost | BlockedPost')


async def the_cursor_walk() -> None:
  """A `_paged` walk: awaited flat, iterated a page at a time, or resumed from a state."""
  async with Bluesky.new() as client:
    walk = client.actor.search_actors_paged(q='bluesky', limit=25)
    reveal_type(walk, expected_text='PaginatedResponse[ProfileView, str]')
    reveal_type(await walk, expected_text='Sequence[ProfileView]')
    async for page in walk:
      reveal_type(page, expected_text='Sequence[ProfileView]')
      reveal_type(page[0]['handle'], expected_text='str')
    # The cursor itself: each page carries the state before it and the state after it,
    # `None` once the walk is done, and `resume` restarts from a saved one.
    async for checkpoint in walk.pages():
      reveal_type(checkpoint, expected_text='Page[ProfileView, str]')
      reveal_type(checkpoint.state, expected_text='str')
      reveal_type(checkpoint.next, expected_text='str | None')
      if checkpoint.next is not None:
        reveal_type(
          walk.resume(checkpoint.next), expected_text='PaginatedResponse[ProfileView, str]'
        )
    feed = client.feed.get_author_feed_paged(actor='bsky.app', filter='posts_no_replies')
    reveal_type(feed, expected_text='PaginatedResponse[FeedViewPost, str]')
    reveal_type(await feed, expected_text='Sequence[FeedViewPost]')
    reveal_type(
      client.graph.get_followers_paged(actor='atproto.com'),
      expected_text='PaginatedResponse[ProfileView, str]',
    )


async def the_firehose() -> None:
  """A Jetstream subscription: one manager, events typed by their `kind`."""
  async with Bluesky.new() as client:
    subscription = client.jetstream.events(wanted_collections=['app.bsky.feed.post'])
    reveal_type(subscription, expected_text='StreamManager[JetstreamEvent, Any, Any]')
    async with subscription as stream:
      async for event in stream:
        reveal_type(event, expected_text='JetstreamEvent')
        reveal_type(event['kind'], expected_text="Literal['commit', 'identity', 'account']")
        reveal_type(event['time_us'], expected_text='datetime')
        commit = event.get('commit')
        reveal_type(commit, expected_text='Commit | None')
        if commit is not None:
          reveal_type(commit['collection'], expected_text='str')
          reveal_type(commit.get('record'), expected_text='dict[str, Any] | None')


async def raw_bodies() -> None:
  """`validate=False` returns the body as the wire sent it, and says so: `Any`."""
  async with Bluesky.new() as client:
    raw = await client.actor.get_profile('bsky.app', validate=False)
    reveal_type(raw, expected_text='Any')
    reveal_type(await client.feed.search_posts(q='atproto', validate=False), expected_text='Any')
    # The whole recursive thread, not the one level around the root the schema types.
    reveal_type(
      await client.feed.get_post_thread(
        'at://did:plc:example/app.bsky.feed.post/1', validate=False
      ),
      expected_text='Any',
    )
    # A walk carries the same flag down to each page, and its row type with it.
    reveal_type(
      client.actor.search_actors_paged(q='bluesky', validate=False),
      expected_text='PaginatedResponse[Any, str]',
    )
    # The stream is the exception: it has no `Literal[False]` overload, so an unvalidated
    # subscription still reads as `JetstreamEvent` while it yields raw frames. NOTES.md 4.
    events = client.jetstream.events(wanted_collections=['app.bsky.feed.post'], validate=False)
    reveal_type(events, expected_text='StreamManager[JetstreamEvent, Any, Any]')
