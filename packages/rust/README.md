<!-- nav:start -->
<table>
  <tr>
    <td align="center"><a href="../../README.md">🐍 Python</a></td>
    <td align="center"><a href="../typescript/README.md">🟦 TypeScript</a></td>
    <td align="center"><b>🦀 Rust</b></td>
    <td align="center"><a href="https://github.com/truewire-dev/bluesky/tree/main/spec">📐 The spec</a></td>
    <td align="center"><a href="../../NOTES.md">🧩 Toolchain gaps</a></td>
  </tr>
</table>
<!-- nav:end -->

# Bluesky, in Rust

A typed, validated async client for [Bluesky](https://bsky.app)'s public AT Protocol API
and the Jetstream firehose. Same spec as the [Python](../../README.md) and
[TypeScript](../typescript/README.md) clients, same recordings, same twelve endpoints.

## Build

Not on crates.io yet. The package is `truewire-bluesky` -- `bluesky` there is squatted by
an unrelated placeholder -- and the library it exposes is `bluesky`, so a git dependency
has to name both:

```toml
[dependencies]
bluesky = { package = "truewire-bluesky", git = "https://github.com/truewire-dev/bluesky" }
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
```

Or from a checkout:

```bash
cd packages/rust && cargo build
```

`truewire-core`, the runtime under it, comes from crates.io.

## The client

`Bluesky::new` takes the two transports it is built from — the AppView's HTTP core and the
Jetstream socket — because the root composes them. Nothing connects until you call
something.

```rust
use std::sync::Arc;
use bluesky::core::{Core, CoreOptions, JetstreamCore, JetstreamOptions};
use bluesky::Bluesky;
use truewire_core::CallOptions;

let client = Bluesky::new(
    Arc::new(Core::new(CoreOptions::default())),
    Arc::new(JetstreamCore::new(JetstreamOptions::default())),
);

let profile = client
    .actor
    .get_profile(
        bluesky::actor::get_profile::Request { actor: "bsky.app".into(), ..Default::default() },
        CallOptions::default(),
    )
    .await?;
println!("{} {:?}", profile.handle, profile.followers_count);
```

`CoreOptions` takes a `base_url` (the public AppView by default, `bsky.social` when an
`access_jwt` is given, a `truewire mock` address in tests) and an optional `access_jwt`.
None of the endpoints here needs one.

Every request struct carries an `extra` field for what the spec does not name, so a struct
literal ends in `..Default::default()`.

## Calling an endpoint

Each method takes its request struct and `CallOptions`, and returns the validated response.
A `_raw` twin returns the wire body as `serde_json::Value` for the fields the spec does not
model.

```rust
let feed = client
    .feed
    .get_author_feed(
        bluesky::feed::get_author_feed::Request {
            actor: "bsky.app".into(),
            limit: Some(3),
            filter: Some(bluesky::feed::get_author_feed::RequestFilter::PostsNoReplies),
            ..Default::default()
        },
        CallOptions::default(),
    )
    .await?;
for entry in &feed.feed {
    println!("{} {}", entry.post.indexed_at, entry.post.record.text);
}
```

Six endpoints are cursor-paged and each has a `_paged` twin returning a
`PaginatedResponse`: await it for every row, or walk `rows()`/`pages()` one page at a time.

## Subscribing to Jetstream

`events` returns a `Stream` of decoded events. Each subscription owns its connection,
because Jetstream has no subscribe frame: the parameters travel in the connection URL, the
server pushes from the moment the socket is accepted, and closing it is what unsubscribes.

```rust
use futures::StreamExt;

let stream = client
    .jetstream
    .events(
        bluesky::jetstream::events::Parameters {
            wanted_collections: Some(vec!["app.bsky.feed.post".into()]),
            ..Default::default()
        },
        CallOptions::default(),
    )
    .await?;
futures::pin_mut!(stream);
while let Some(event) = stream.next().await {
    let event = event?;
    println!("{} {}", event.time_us, event.did);
}
```

Dropping the stream closes the connection, so there is no lifecycle to manage beyond
scope: this is the one place Rust needs less ceremony than the other two languages.

## Tests

```bash
cargo test
```

The tests replay the recorded examples through this client against `truewire mock`, which
serves the same recordings over real HTTP and a real WebSocket. Nothing touches the
network, and the assertions are the ones the Python and TypeScript suites make about the
same responses.

Every example on this page is compiled by `tests/readme_examples.rs`, which is the same
code with the async function a snippet leaves implicit. It caught a name that did not exist
the first time it ran.

## What is hand-written

`truewire generate rust` writes the endpoints, the routers, the types and the client. It
never writes the transports:

- [`src/bluesky/core/mod.rs`](src/bluesky/core/mod.rs) — the AppView transport and the XRPC
  error mapping. A list-valued parameter (`uris`, `actors`) is sent as repeated query keys;
  the runtime's `query_from` would send the array as one JSON string.
- [`src/bluesky/core/jetstream.rs`](src/bluesky/core/jetstream.rs) — a `Dialect` that
  declines both request verbs with `Outgoing::Nothing`, which is what the runtime's `push`
  stream is for, and one connection per subscription.
