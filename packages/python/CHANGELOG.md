# Changelog

## 0.1.0

First release.

- **Twelve read endpoints**, none of which need an account: profiles, posts, threads,
  feeds, the follow graph and handle resolution, from the public AppView.
- **Jetstream**, Bluesky's public firehose of repository events, over one WebSocket per
  subscription.
- **Four write endpoints**: sessions and records, so the client can post. An app password
  is exchanged for a session and refreshed automatically, and no credential is ever a
  request parameter -- the transport injects them, which is what keeps them out of every
  recorded example.
- **`core.richtext`** computes the link facets Bluesky needs to render a URL as a link,
  with the UTF-8 byte offsets it insists on.
- Every endpoint carries a recorded request/response pair captured from the live API
  through this same client, replayed by the test suite against a local mock. The one
  exception, `feed.search_posts`, declares why it has none: the public AppView answers it
  with 403 to a caller without a session.
