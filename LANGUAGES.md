# The same client in two languages

One spec, two backends. This branch generates `spec/` into Python and TypeScript so the
two can be read side by side: the same twelve endpoints, the same recorded examples, the
same docstrings, two sets of idioms.

Rust is not here. The backend renders only a simple root, and this client's root is
composite -- HTTP for ten endpoints, a WebSocket for Jetstream -- so it produces types and
no client at all; no published release carries the backend either, so declaring `[rust]`
in `truewire.toml` only broke CI. It comes back when it can render this client, not
before. The gap is on `docs/plan.md` in the toolchain repository.

It exists to be reviewed by a person. Every gate we have proves the generated code is
*correct*; nothing we have proves it is *good*, and nobody with taste has read a line of the
TypeScript.

Regenerate either with `truewire generate <python|typescript>`.

## What came out

| | files | client | endpoints | Jetstream |
| --- | --- | --- | --- | --- |
| Python (`src/bluesky`) | 24 | yes | 11 | yes |
| TypeScript (`ts/src/bluesky`) | 21 | yes | 11 | yes |

## Where to look

**Python** is the mature one and the baseline. `src/bluesky/feed/get_posts.py` is a
one-call endpoint; `src/bluesky/feed/get_author_feed.py` has the paged twin;
`src/bluesky/jetstream/events.py` is the stream.

**TypeScript** is worth the most of your attention, because it is complete enough to judge
and new enough to change. Read in this order:

- `ts/src/bluesky/main.ts` — the root. Note that it takes a hand-written `BlueskyCore` by
  shape (`client: HttpEndpoint<DefaultMeta>`, `socket: StreamEndpoint`) rather than
  extending a base class the way Python does.
- `ts/src/bluesky/feed/get_posts.ts` — one endpoint end to end: the request interface, the
  runtime codec beside it under the same name, the `validate: false` overload, the call.
- `ts/src/bluesky/types/index.ts` — every shared schema, including the open `EmbedView`
  union.

Questions I cannot answer for myself: is a request *object* right where Python takes
flat keyword arguments? Is declaring `interface Request` and `const Request: Codec<Request>`
under one name clever or confusing? Do the overloads read well at a call site?

## It runs

`cd ts && yarn install && yarn test` replays all eleven recorded examples through the
generated TypeScript client against `truewire mock`, which serves the same recordings over
real HTTP and a real WebSocket. Eleven tests, and CI runs them.

The hand-written core is `ts/src/bluesky/core/`, and it is the other half worth your eye,
because none of it is generated:

- `http.ts` — the AppView transport and the XRPC error mapping. Repeated query keys for a
  list-valued parameter (`uris`, `actors`) are the one wire detail that is easy to get
  wrong and invisible until a real call.
- `jetstream.ts` — one WebSocket per subscription, and nothing sent on it. Jetstream has no
  subscribe frame: the parameters are the connection URL's query string, and closing the
  socket is what unsubscribes.
- `index.ts` — `newBluesky()`, the only place the generated client and the hand-written
  transports meet.

`ts/test/replay.test.ts` asserts the same things about the same responses that
`test/test_recordings.py` does, so the two clients cannot quietly diverge.
