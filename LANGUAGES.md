# The same client in three languages

One spec, three backends. This branch generates `spec/` into Python, TypeScript and Rust
so the three can be read side by side: the same twelve endpoints, the same recorded
examples, the same docstrings, three sets of idioms.

It exists to be reviewed by a person. Every gate we have proves the generated code is
*correct*; nothing we have proves it is *good*, and for TypeScript and Rust nobody with
taste has read a line of it.

Regenerate any of them with `truewire generate <python|typescript|rust>`.

## What came out

| | files | client | endpoints | Jetstream |
| --- | --- | --- | --- | --- |
| Python (`src/bluesky`) | 24 | yes | 11 | yes |
| TypeScript (`ts/src/bluesky`) | 21 | yes | 11 | yes |
| Rust (`rust/src/bluesky`) | 3 | **no** | **0** | no |

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

**Rust** is three files and no client, which is the honest state of it. The backend renders
only a simple root; Bluesky's root is composite (HTTP for ten endpoints, a WebSocket for
Jetstream), so the root router is skipped and the reachability filter then drops the eleven
endpoints that had already rendered. `truewire generate rust` says so now — it did not
before this branch. `rust/src/bluesky/types/mod.rs` is real and worth a look; there is
nothing else to review yet.

A composite root is not exotic: it is what any client speaking both HTTP and WebSocket
needs, which is the combination Truewire exists for. It is on `docs/plan.md` as the gap
that decides whether Rust is a supported backend or a demo.

## Not done here

The TypeScript client has no hand-written core yet, so it type-checks and reads but does
not run. Writing it — an `HttpEndpoint` for `public.api.bsky.app` and a `StreamEndpoint`
for Jetstream — is the next step, and would let the TypeScript client replay the same
recordings against `truewire mock` that the Python one does.
