"""Facets: the byte ranges that make part of a post's text a link.

Bluesky does not auto-link anything. A post whose text contains a URL renders as plain
text unless the record also carries a `facets` list saying which bytes of that text are a
link and where it points. Every client has to compute them, and this is that.

The trap, and the reason this is a module with tests rather than three lines at a call
site: **the offsets are UTF-8 byte positions, not character positions.** A post containing
an em dash, an arrow or any emoji before the URL has more bytes than characters, so
character indices silently point at the wrong place -- and the failure is not an error. The
facet is accepted, and the link lands a few bytes off, cutting the URL short or swallowing
the character before it.

That is exactly how this client's own first thread shipped without a working link.

Only links are computed here. Mentions (`@handle`) additionally need the handle resolved to
a DID before the facet can be written, and hashtags are a third shape; neither is needed
yet, and guessing at them would be worse than leaving them out.
"""

import re
from typing_extensions import Any, TypedDict

URL = re.compile(
  r"""
  (?<![\w@.])                     # not mid-word, and not an email's domain
  (
    https?://[^\s<>"]+            # an explicit scheme, or
    |
    (?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+   # a bare domain: labels, then
    (?:com|org|net|dev|io|app|sh|co|ai|social|xyz|me)  # a TLD we are willing to guess at
    \b
    (?:/[^\s<>"]*)?               # and an optional path
  )
  """,
  re.VERBOSE | re.IGNORECASE,
)
"""A URL in post text, with or without a scheme.

The bare-domain branch is deliberately conservative: it takes a short list of TLDs rather
than any dotted word, because `e.g` and `Node.js` are not links and a client that turns
them into one is worse than a client that misses a real one. A caller who wants the
guessing to stop can always write the scheme.
"""

TRAILING = '.,;:!?)]}\'"'
"""Punctuation that ends a sentence rather than a URL. `truewire.dev.` links `truewire.dev`."""


class ByteSlice(TypedDict):
  """The half-open byte range a facet covers, as `app.bsky.richtext.facet#byteSlice`."""

  byteStart: int
  byteEnd: int


class Facet(TypedDict):
  """One `app.bsky.richtext.facet`: a range, and what it means."""

  index: ByteSlice
  features: list[dict[str, Any]]


def links(text: str) -> list[Facet]:
  """Every URL in `text`, as link facets with correct UTF-8 byte offsets.

  Returns an empty list when there is nothing to link, which is what a post record should
  then carry -- an empty `facets` is accepted but says nothing, so omit it.

  Args:
    text: The post's text, exactly as it will be sent. Offsets are meaningless against
      anything else, so compute facets from the final string and never from a draft.
  """
  facets: list[Facet] = []
  for match in URL.finditer(text):
    found = match.group(1).rstrip(TRAILING)
    if not found:
      continue
    start = match.start(1)
    # The offsets are byte positions, so the prefix is measured in bytes, not characters.
    byte_start = len(text[:start].encode('utf-8'))
    byte_end = byte_start + len(found.encode('utf-8'))
    facets.append(
      Facet(
        index=ByteSlice(byteStart=byte_start, byteEnd=byte_end),
        features=[
          {
            '$type': 'app.bsky.richtext.facet#link',
            'uri': found if '://' in found else f'https://{found}',
          }
        ],
      )
    )
  return facets


def post(text: str, *, created_at: str, langs: list[str] | None = None) -> dict[str, Any]:
  """An `app.bsky.feed.post` record, with its links already faceted.

  A convenience over `repo.create_record`, not a replacement for it: the record is an
  ordinary dict, and a caller who wants an embed, a reply or a quote adds it to what comes
  back.

  Args:
    text: The post's text.
    created_at: RFC 3339 timestamp, `Z`-suffixed.
    langs: BCP 47 language tags. Worth setting -- it drives translation and the "show
      posts in your languages" filter, and a post without it reaches fewer people.
  """
  record: dict[str, Any] = {'$type': 'app.bsky.feed.post', 'text': text, 'createdAt': created_at}
  if langs:
    record['langs'] = langs
  found = links(text)
  if found:
    record['facets'] = found
  return record
