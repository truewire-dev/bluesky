/**
 * Every recorded example replayed through the generated TypeScript client against
 * `truewire mock`, which serves the same recordings over real HTTP and a real WebSocket.
 * Nothing here touches the network.
 *
 * The assertions are the TypeScript half of `test/test_recordings.py`: structural, not
 * literal, because the counts and the text move with every re-recording and the shape
 * does not. Where Python proves a thing about a response, this proves the same thing
 * about the same response, so a divergence between the two clients is a test failure
 * rather than a discovery months later.
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { beforeAll, afterAll, describe, expect, inject, it } from 'vitest'
import { newBluesky, type Client } from '../src/bluesky/core/index.js'
import type { PostView, ProfileView, ProfileViewBasic } from '../src/bluesky/types/index.js'
import { projectRoot } from './setup.js'

const BSKY_APP = 'bsky.app'
const ATPROTO = 'atproto.com'

/**
 * The post `feed.getPosts` and `feed.getPostThread` both name, read from the example
 * rather than written here: a post can be deleted and `test/refresh_post_examples.py`
 * repoints both examples at a live one, which a constant would turn into a failure.
 */
const PINNED_POST: string = (
  JSON.parse(
    readFileSync(
      path.join(projectRoot, 'spec/endpoints/feed/get_posts/examples/bsky_app_hello.request.json'),
      'utf8',
    ),
  ) as { request: { uris: string[] } }
).request.uris[0]!

/** Every profile view carries the two identifiers that make an account addressable. */
function isProfile(profile: ProfileView | ProfileViewBasic): void {
  expect(profile.did.startsWith('did:')).toBe(true)
  expect(profile.handle).toBeTruthy()
}

/** Every post view carries its address, its author and the record as written. */
function isPost(post: PostView): void {
  expect(post.uri.startsWith('at://')).toBe(true)
  expect(typeof post.cid).toBe('string')
  isProfile(post.author)
  expect(typeof post.record.text).toBe('string')
}

let client: Client

beforeAll(() => {
  client = newBluesky({ baseUrl: inject('httpBaseUrl'), wsUrl: inject('wsUrl') })
})

afterAll(async () => {
  await client[Symbol.asyncDispose]()
})

describe('recorded HTTP examples replay through the generated client', () => {
  it('actor.getProfile', async () => {
    const view = await client.bluesky.actor.getProfile({ actor: BSKY_APP })
    expect(view.handle).toBe(BSKY_APP)
    expect(view.did.startsWith('did:')).toBe(true)
    expect(view.followersCount!).toBeGreaterThan(0)
    expect(view.postsCount!).toBeGreaterThan(0)
  })

  it('actor.getProfiles', async () => {
    const views = await client.bluesky.actor.getProfiles({ actors: [BSKY_APP, ATPROTO] })
    expect(views.profiles.map(view => view.handle)).toEqual([BSKY_APP, ATPROTO])
    for (const view of views.profiles) isProfile(view)
  })

  it('actor.searchActors', async () => {
    const results = await client.bluesky.actor.searchActors({ q: 'bluesky', limit: 3 })
    expect(results.actors.length).toBeGreaterThan(0)
    expect(results.actors.length).toBeLessThanOrEqual(3)
    for (const view of results.actors) isProfile(view)
  })

  it('feed.getAuthorFeed', async () => {
    const feed = await client.bluesky.feed.getAuthorFeed({
      actor: BSKY_APP, limit: 3, filter: 'posts_no_replies',
    })
    expect(feed.feed.length).toBeGreaterThan(0)
    expect(feed.feed.length).toBeLessThanOrEqual(3)
    for (const entry of feed.feed) {
      isPost(entry.post)
      expect(entry.reply).toBeUndefined()
    }
  })

  it('feed.getFeed', async () => {
    const feed = await client.bluesky.feed.getFeed({
      feed: 'at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.generator/whats-hot', limit: 3,
    })
    expect(feed.feed.length).toBeGreaterThan(0)
    for (const entry of feed.feed) isPost(entry.post)
  })

  it('feed.getPostThread', async () => {
    const thread = await client.bluesky.feed.getPostThread({
      uri: PINNED_POST, depth: 2, parentHeight: 0,
    })
    // The thread node is a union of three shapes, and TypeScript will not let the post be
    // read until the tag has been checked -- which Python's TypedDict happily allowed.
    const root = thread.thread
    if (root.$type !== 'app.bsky.feed.defs#threadViewPost') {
      throw new Error(`the recorded thread is a ${root.$type}, not a post`)
    }
    expect(root.post.uri).toBe(PINNED_POST)
    expect(root.post.replyCount!).toBeGreaterThan(0)
  })

  it('feed.getPosts', async () => {
    const result = await client.bluesky.feed.getPosts({ uris: [PINNED_POST] })
    expect(result.posts).toHaveLength(1)
    expect(result.posts[0]!.uri).toBe(PINNED_POST)
    isPost(result.posts[0]!)
  })

  it('graph.getFollowers', async () => {
    const page = await client.bluesky.graph.getFollowers({ actor: ATPROTO, limit: 3 })
    expect(page.subject.handle).toBe(ATPROTO)
    expect(page.followers.length).toBeGreaterThan(0)
    for (const view of page.followers) isProfile(view)
  })

  it('graph.getFollows', async () => {
    const page = await client.bluesky.graph.getFollows({ actor: BSKY_APP, limit: 3 })
    expect(page.subject.handle).toBe(BSKY_APP)
    expect(page.follows.length).toBeGreaterThan(0)
    for (const view of page.follows) isProfile(view)
  })

  it('identity.resolveHandle', async () => {
    const resolved = await client.bluesky.identity.resolveHandle({ handle: BSKY_APP })
    expect(resolved.did.startsWith('did:')).toBe(true)
  })
})

describe('the recorded Jetstream capture replays over a real WebSocket', () => {
  it('pushes typed events, and time_us arrives as a Date', async () => {
    const events: unknown[] = []
    const subscription = client.bluesky.jetstream.events({
      wantedCollections: ['app.bsky.feed.post'],
    })
    const stream = await subscription.open()
    for await (const event of stream) {
      expect(event.did.startsWith('did:')).toBe(true)
      expect(['commit', 'identity', 'account']).toContain(event.kind)
      expect(event.time_us).toBeInstanceOf(Date)
      events.push(event)
      if (events.length >= 5) break
    }
    expect(events.length).toBe(5)
    await stream.unsubscribe()
  })
})
