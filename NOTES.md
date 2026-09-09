<!-- nav:start -->
<table>
  <tr>
    <td align="center"><a href="./README.md">🐍 Python</a></td>
    <td align="center"><a href="./packages/typescript/README.md">🟦 TypeScript</a></td>
    <td align="center"><a href="./packages/rust/README.md">🦀 Rust</a></td>
    <td align="center"><a href="https://github.com/truewire-dev/bluesky/tree/main/spec">📐 The spec</a></td>
    <td align="center"><b>🧩 Toolchain gaps</b></td>
  </tr>
</table>
<!-- nav:end -->

# Notes for the Truewire toolchain

Things Truewire 0.6.0 (`truewire-core` 0.2.1) could not express or do while this client was written, each with the exact error or warning where there was one and what the project does instead. Nothing here was worked around by bending the spec.

## 1. `truewire capture` records HTTP pairs only, so a stream cannot be recorded with it

Jetstream is the one `kind: "stream"` endpoint here, and `capture` refuses it before opening anything:

```text
jetstream.events is not an HTTP rpc endpoint; capture records HTTP request/reply pairs only
  truewire/cli/capture.py:73
```

There is no other subcommand that records a subscription: `mock` replays one, `check` validates one, nothing captures one. So the project records the firehose itself, in `packages/python/test/record_jetstream.py`: it resolves the endpoint's generated method the same way `capture` does (`truewire.examples.resolve_endpoint_function`), binds the recorded `examples/<id>.parameters.json` with `coerce_ws_example_call`, subscribes for a bounded window through the same generated client, and writes what arrived to `examples/<id>.messages.json` — the file `truewire check` validates and `truewire mock` replays. It builds the client with `validate=False`, because a recording has to be the wire body: validated, `time_us` is already a `datetime` and no longer what Jetstream sent.

A `capture` that accepted a stream endpoint with `--seconds`/`--limit` would replace that file exactly. The pieces it would need are all already public.

## 2. A self-referential schema crashes the generator, and `check` does not see it

An AT Protocol thread is recursive on the wire: `thread.parent` and every entry of `thread.replies` are another thread node, to whatever depth `depth`/`parentHeight` asked for. Declaring that directly — `ThreadNode.anyOf[0].properties.parent: {"$ref": "ThreadNode"}` — passes `truewire check` (`Result: OK`, 0 violations) and then kills `truewire generate python`:

```text
RecursionError: maximum recursion depth exceeded while calling a Python object
  truewire/generation/schema/resolve.py:36, rec
```

The resolver has a cycle guard four lines below the crash (`raise ResolutionError(f'Cycle detected at {ref}')`), and it is never reached: the recursion goes through the inline `anyOf` member rather than through a `Reference` the `visited` set is keyed on. So the failure is a stack overflow at generation time rather than a clear error at check time, and it is a real wire shape, not a pathological one.

The spec therefore declares `ThreadNode` as a non-recursive stand-in: the post one level from the root, with `additionalProperties: true` so the deeper `parent`/`replies` validate and are dropped from the validated value. The endpoint's `notes` and the README both say a validated thread keeps one level around the root and that `validate=False` returns the whole tree. Either the cycle guard should catch this shape and report it, or a self-reference should render as a forward-referenced recursive `TypedDict`, which Python supports.

## 3. One titled `anyOf` member becomes one type per use site

`ThreadNode`'s first member is a titled inline object (`ThreadPost`). It is referenced from three places — the response's `thread`, a node's `parent`, an entry of `replies` — and the generator emits it three times under three names:

```text
ThreadPostThreadNodeAnyOf0, ThreadPostParentAnyOf0, ThreadPostItemAnyOf0
```

The bodies are identical. A caller reading a thread sees three names for one wire shape, and `truewire standards`' duplicate-schema check (S6) passes, because they are distinct declarations rather than a repeated one. Nothing here works around it; the union is usable and the names are the only cost. Rendering a titled inline object once, keyed by its `title`, and referring to it from each use site would fix it.

## 4. A generated stream method has no `Literal[False]` overload for `validate`

Every rpc method overloads on `validate`, so `validate=False` returns `Any` and pyright knows the caller is holding the raw body. The stream method does not:

```python
client.jetstream.events(wanted_collections=['app.bsky.feed.post'], validate=False)
# StreamManager[JetstreamEvent, Any, Any]
```

At runtime that subscription yields raw frames — `time_us` an `int`, not the `datetime` `JetstreamEvent` declares — so the type is wrong in exactly the case the flag exists for. `packages/python/test/typing_usage.py` asserts what the generator actually produces, with this note beside it, rather than asserting what it should produce. `packages/python/test/record_jetstream.py` is the one place the project relies on the unvalidated events, and it treats them as `Any`. The same pair of overloads the rpc path already emits would close it.

## 5. `jetstream.events` declares no `envelope.verb`, and cannot

`truewire check` warns, on every run:

```text
ADR 0004 — a `kind: "stream"` endpoint declares how its frames state subscribe/unsubscribe intent [warning]
   jetstream.events  envelope.verb: this stream endpoint declares no `envelope.verb`, so the mock server cannot tell a subscribe frame from an unsubscribe frame for it without guessing -- declare `{"path": ..., "subscribe": ..., "unsubscribe": ...}` naming the field (and its two literal values) that states intent on this API's wire frame
```

There is nothing to declare. Jetstream has no subscribe frame at all: a consumer opens `wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=...&cursor=...` and the server pushes from the moment the socket is accepted; no frame ever travels the other way, and closing the socket is what unsubscribes. There is no field on any frame that states an intent, so any `envelope.verb` here would be invented, and inventing one to silence a check would put a lie in the spec and a matching lie in the mock. The endpoint declares `push: {trigger: connect}` instead, which is the honest statement of the same fact, and the mock serves it through that: `start_ws_server` pushes a `ConnectPush` example's messages the instant the connection is accepted, which is exactly what Jetstream does. The client core says the same thing in code — `Connection.request_subscription` and `request_unsubscription` both send nothing.

The warning stands, unsilenced. The rule could exempt an endpoint that declares `push: connect`, since a stream with no outgoing frame has no verb to name.

## 6. `truewire capture` leaves a stale `unverified` declaration behind

`capture` writes the pair and runs `check`, but leaves the endpoint's `unverified` block, and `truewire examples` then fails unconditionally:

```text
2 endpoint(s) declare `unverified` despite having paired examples; remove the stale declaration (rerun with --verbose to list them)
```

The recording script (`packages/python/test/recapture.sh`) ends with `packages/python/test/verified.py`, which drops the block of every endpoint that has a pair — a request beside a response for the eleven XRPC endpoints, parameters beside messages for the stream; `--check` is the strict CI gate that lists endpoints still without one. `capture` could drop the block itself, since it knows the pair it just wrote, or `examples` could offer `--fix`.

## 7. `truewire check` reads a bare string as a probable closed set

The second standing warning:

```text
2. Closed sets use `enum` [warning]
   jetstream.events  responses.200.content.application/json.schema.properties.account.properties.status: `status` may be a closed set. Declare `enum` if the API documents its values; leave it bare if it does not — a guessed `enum` becomes a `Literal` that rejects values the API later sends
```

`status` on an account event is `takendown`, `suspended`, `deleted` or `deactivated` today, but the lexicon lists those as known values, not a closed set, and an unknown one is expected to be passed through rather than rejected. An `enum` would render a `Literal` that raises on the next value the network adds — which is the failure mode the warning's own text describes. It is left bare and the warning stands. The rule has no way to say "open set, known values", which is the shape a lexicon actually declares.

## 8. `truewire generate python --check` compares the manifest, not the content

For Python, `--check` prints `Codegen manifest matches bluesky (20 files).` and passes even when an owned file's body is stale (the TypeScript path compares content). CI runs `generate` again and `git diff --exit-code -- src .truewire` after `--check`.

## 9. `generate --check` needs a manifest the scaffold gitignores

`truewire init` writes `.gitignore` with `.truewire/` in it (`truewire/cli/init.py:111`), and `generate --check` on a checkout without the manifest fails:

```text
Cannot check generated files: missing manifest .truewire/python-files.json
```

so CI could never run the check on a fresh clone. This repository commits `.truewire/python-files.json` — a short, deterministic list of the files the generator owns — and CI diffs it together with `src` after regenerating. Either the manifest should be scaffolded as committed, or `--check` should rebuild the expected file list from the spec and compare it with the tree.

## 10. Generated import order does not satisfy ruff's isort rule

The generator writes `from truewire_core...`, `from typing_extensions...`, then first-party imports without the blank line and ordering ruff's `I001` wants (its shipped `resources/ruff.toml` selects only `TID251`):

```text
I001 [*] Import block is un-sorted or un-formatted
 --> src/bluesky/jetstream/events.py:2:1
```

A project that lints with `I` needs per-file ignores for the generated modules, as `ruff.toml` here has. Emitting isort-sorted imports would remove that.

## 11. The WebSocket runtime assumes a frame-based subscribe protocol

`truewire_core.ws.Streams` is built for a socket that multiplexes: one connection carries many subscriptions, `request_subscription`/`request_unsubscription` send a frame naming a channel, and `parse_msg` routes each incoming frame back to the channel it belongs to. Jetstream is the other shape — one subscription per connection, its filter in the URL, nothing ever sent — so the core here implements the base class by declining it: both request methods return `None`, `parse_msg` routes every frame to the single channel the connection carries, and `SocketClient` opens a fresh `Connection` per subscription and closes it on unsubscribe (`packages/python/src/bluesky/core/ws.py`). That works and is small, but it is a subclass whose contract is "none of the above". A `Streams` variant for URL-parameterised, single-subscription sockets would let a core this shape declare what it is instead of overriding three methods to do nothing.

## 12. A union is closed by construction, and nothing says it could be open

The AppView hydrates a post's embed as one of five `$type`-tagged shapes, so `EmbedView` was written as an `anyOf` of the five. On 9 September a recording run walked fifty posts from `bsky.app` and the twenty-ninth carried `app.bsky.embed.gallery#view`, a sixth shape that was not there when the spec was written. Every declared member is tried, each fails on the `$type` `Literal`, and the whole page is rejected:

```text
truewire_core.exceptions.ValidationError: 12 validation errors for AuthorFeed
feed.28.post.embed.ImagesView.$type
  Input should be 'app.bsky.embed.images#view' [type=literal_error, input_value='app.bsky.embed.gallery#view', input_type=str]
  ...
```

One post the client could not name cost the other forty-nine, which is the wrong trade for a feed. There is no way to say *this union is open*, so the spec adds the escape hatch by hand: a last member `UnknownEmbedView`, `{"$type": string}` with `additionalProperties: true`. Pydantic's smart union still prefers a member whose `$type` `Literal` matches, so a modelled embed keeps its whole payload and only an unmodelled one falls through, keeping its tag and losing its body. That is the behaviour wanted; the cost is that it is invisible in the schema, and that a *malformed* member of the five now lands in the catch-all instead of raising.

`truewire check` has a warning for the mirror image of this — rule 2, a bare string that may be a closed set, worded as "a guessed `enum` becomes a `Literal` that rejects values the API later sends" — and none for a union closed by construction, which is the same hazard with the same cause. A declared `"open": true` on an `anyOf` (rendering the fallback member, and saying so in the generated docstring) would express it once, and `check` could warn on a union of `$type`-tagged members that has no fallback, the way it warns on a bare string.

## 13. A generated TypeScript client cannot be given a constructor or a lifecycle

Python's generated client extends a hand-written base, named in `truewire.toml`:

```toml
[python.cores.root]
base = "bluesky.core:ClientBase"
```

so `Bluesky.new(base_url=..., access_jwt=...)` and `async with Bluesky.new() as client` are the base class's, and the generated client inherits both. A caller writes what they would write for any Python client.

The TypeScript backend has no `base`. It takes its core by shape (ADR 0011: the generator never imports the target package), which is the right call for the *transports* and leaves nothing for the two things that are not transports -- a factory that knows the default hosts, and disposal that closes the sockets. The generated class is final in practice: adding either means editing generated code.

The first attempt was a free function returning a bag, and it reads as badly as it sounds:

```ts
const { bluesky } = newBluesky()   // why is this an object?
```

What the project does instead is subclass in the other direction -- `core/client.ts` extends the generated `Bluesky`, adds `static new` and `[Symbol.asyncDispose]`, and the package's `exports` map points `.` at the hand-written entry so a caller importing the package gets the subclass rather than the generated class of the same name. It works, and `Bluesky.new()` now reads the same in both languages, but the two arrive there by opposite routes and the TypeScript one costs a name shadow and an `exports` remap that every TypeScript project generated by Truewire will pay.

A `base` for the TypeScript backend would close it: `[typescript.cores.root] base = "./core/base.js#ClientBase"` rendering `export class Bluesky extends ClientBase`, exactly as Python does. The same gap applies to Rust, which will want `impl` blocks on the generated client for the same two reasons. Found by a reviewer reading the docs and asking why the constructor looked like that.
