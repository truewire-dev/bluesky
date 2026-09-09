<!-- nav:start -->
<table>
  <tr>
    <td align="center"><a href="../../README.md">🐍 Python</a></td>
    <td align="center"><b>🟦 TypeScript</b></td>
    <td align="center"><a href="https://github.com/truewire-dev/bluesky/tree/main/spec">📐 The spec</a></td>
    <td align="center"><a href="../../NOTES.md">🧩 Toolchain gaps</a></td>
  </tr>
</table>
<!-- nav:end -->

# Bluesky, in TypeScript

A typed, validated client for [Bluesky](https://bsky.app)'s public AT Protocol API and the
Jetstream firehose. Same spec as [the Python client](../../README.md), same recordings,
same twelve endpoints — the idioms are the only thing that differs.

Every response is validated against the shape the API was *recorded* sending, not the shape
someone remembered.

## Install

Not published yet. From a checkout:

```bash
cd packages/typescript && yarn install
```

## The client

`newBluesky()` builds the two transports and the generated client over them. Nothing
connects until you call something.

```ts
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

const profile = await bluesky.actor.getProfile({ actor: 'bsky.app' })
console.log(profile.handle, profile.followersCount)
```

Every option has a default that works, so the common case takes none:

```ts
import { newBluesky } from '@truewire/bluesky/core'

const client = newBluesky({
  // The XRPC host. Defaults to the public AppView, or to bsky.social when `accessJwt` is
  // given. Point it at `truewire mock` in tests.
  baseUrl: 'https://public.api.bsky.app',
  // The Jetstream instance. Each subscription opens its own connection to it.
  wsUrl: 'wss://jetstream2.us-east.bsky.network/subscribe',
  // Validate responses and pushed events against their declared types. A call's own
  // `validate` overrides this.
  validate: true,
})

await client[Symbol.asyncDispose]()
```

No credentials are needed: the public AppView answers eleven of the twelve endpoints
without one. An `accessJwt` from `com.atproto.server.createSession` is accepted and sent as
a bearer token, which is what fills the viewer's own state on a profile against
`bsky.social`.

## Calling an endpoint

Every method takes one request object whose keys are the wire's own parameter names, and
returns the validated response. Optional parameters are optional; required ones are not,
and leaving one out is a compile error.

```ts
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

const feed = await bluesky.feed.getAuthorFeed({
  actor: 'bsky.app',
  limit: 3,
  filter: 'posts_no_replies', // a Literal of the four values the lexicon lists
})

for (const entry of feed.feed) {
  console.log(entry.post.indexedAt.toISOString(), entry.post.record.text.slice(0, 48))
}
```

`indexedAt` is a `Date`, not a string: the spec declares it as RFC 3339 and the codec
parses it. The same is true of `createdAt`, and of Jetstream's `time_us`, which arrives as
Unix microseconds and comes back as a `Date`.

### Paging

Six endpoints are cursor-paged, and each has a `*Paged` twin. It is both awaitable and
async-iterable: `await` it to flatten every page, iterate it to take one page at a time.

```ts
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

// Every follower, as one array. The walk stops when the API stops sending a cursor.
const everyone = await bluesky.graph.getFollowersPaged({ actor: 'atproto.com' })

// Or a page at a time, which is what you want for a large account.
for await (const page of bluesky.graph.getFollowersPaged({ actor: 'atproto.com' })) {
  console.log(page.length, 'followers in this page')
  break
}
```

The paged request type is the endpoint's own request without `cursor`, because the walk
owns the cursor.

### Escaping the types

`validate: false` returns the parsed body exactly as it came, typed `unknown`. That is how
you reach a field the spec does not name — a new embed type, an undeclared extension —
without waiting for the spec to catch up.

```ts
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

const raw: unknown = await bluesky.feed.getPosts(
  { uris: ['at://did:plc:z72i7hdynmk6r22z27h6tvur/app.bsky.feed.post/3juzlwllznd24'] },
  { validate: false },
)
```

## Subscribing to Jetstream

Jetstream is the firehose of every repository commit, identity change and account status
change on the network. `events()` returns a `Subscription`, and nothing connects until you
open or iterate it.

```ts
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

// `await using` closes the socket when the block ends, however it ends.
await using events = bluesky.jetstream.events({
  wantedCollections: ['app.bsky.feed.post'],
})

for await (const event of events) {
  if (event.kind !== 'commit' || event.commit === undefined) continue
  console.log(event.time_us.toISOString(), event.did, event.commit.collection)
  break
}
```

Each subscription owns its connection, because Jetstream has no subscribe frame: the
parameters travel in the connection URL, the server pushes from the moment the socket is
accepted, and closing the socket is what unsubscribes. Two subscriptions want two
different query strings, so they cannot share one connection.

To resume where a previous run stopped, pass the last `time_us` you saw as the cursor:

```ts
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

const since = new Date(Date.now() - 60_000)
await using events = bluesky.jetstream.events({
  wantedCollections: ['app.bsky.feed.post'],
  cursor: since, // declared as Unix microseconds; a Date is rendered to them
})

for await (const event of events) {
  console.log(event.time_us.toISOString())
  break
}
```

## Lifecycle

There are two things to own, and they behave differently.

**HTTP owns nothing.** The transport is `fetch`; there is no pool to open or close, so a
client you only make calls with never needs disposing.

**A subscription owns a socket.** Three ways to close it, in order of preference:

```ts
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

// 1. `await using`: closed at the end of the block, on an exception or an early return.
{
  await using events = bluesky.jetstream.events({ wantedCollections: ['app.bsky.feed.post'] })
  for await (const event of events) { void event; break }
}

// 2. Open it yourself and unsubscribe when done.
const subscription = bluesky.jetstream.events({ wantedCollections: ['app.bsky.feed.post'] })
const stream = await subscription.open()
await stream.unsubscribe()

// 3. Close every socket the client still holds, whatever opened them.
const client = newBluesky()
await client[Symbol.asyncDispose]()
```

Breaking out of a `for await` closes the socket too: the generator's `finally` runs on an
early exit, so the loop above leaks nothing even without `await using`.

## Errors

A non-2xx XRPC answer becomes a typed error carrying the API's own `error` and `message`.
`400` is a `BadRequest`, `401`/`403` an `AuthError`, `429` a `RateLimited`, anything else
an `ApiError`; a response that does not match its declared shape is a `ValidationError`.

```ts
import { isTruewireError, RateLimited } from '@truewire/core'
import { newBluesky } from '@truewire/bluesky/core'

const { bluesky } = newBluesky()

try {
  await bluesky.identity.resolveHandle({ handle: 'nobody.invalid' })
} catch (e) {
  if (e instanceof RateLimited) console.error('backing off')
  else if (isTruewireError(e)) console.error(e.message)
  else throw e
}
```

## Tests

```bash
yarn typecheck
yarn test
```

The tests replay every recorded example through this client against `truewire mock`, which
serves the same recordings over real HTTP and a real WebSocket. Nothing touches the
network, and the assertions are the ones the Python suite makes about the same responses,
so the two clients cannot quietly diverge.

Every ```ts block on this page is compiled against the package by `test/readme.test.ts`.

## What is hand-written

`truewire generate typescript` writes the endpoints, the routers, the types and the codecs.
It never writes the transport, which is the part that knows the API's habits:

- [`src/bluesky/core/http.ts`](src/bluesky/core/http.ts) — the AppView transport and the
  XRPC error mapping. A list-valued parameter (`uris`, `actors`) is sent as repeated query
  keys, which is the one wire detail that is easy to get wrong and invisible until a real
  call.
- [`src/bluesky/core/jetstream.ts`](src/bluesky/core/jetstream.ts) — one socket per
  subscription, and nothing sent on it.
- [`src/bluesky/core/index.ts`](src/bluesky/core/index.ts) — `newBluesky()`, the only place
  the generated client and the hand-written transports meet.
