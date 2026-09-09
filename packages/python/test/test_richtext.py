"""Facets, and the byte-offset trap they exist around.

The first thread this account posted carried a URL and no facets, so it rendered as plain
text -- the failure that motivated the module. The second failure mode is subtler and is
what most of these tests are about: the offsets are UTF-8 *byte* positions, so any
multibyte character earlier in the post shifts them, and a client using character indices
produces a facet that is accepted and lands in the wrong place.
"""

from bluesky.core.richtext import links, post


def sliced(text: str, facet) -> str:
  """The substring a facet actually covers, decoded back from its byte range."""
  raw = text.encode('utf-8')
  return raw[facet['index']['byteStart'] : facet['index']['byteEnd']].decode('utf-8')


class TestFindingLinks:
  def test_no_url_is_no_facets(self):
    assert links('Map by the error code, not the status.') == []

  def test_a_bare_domain_with_a_path(self):
    [facet] = links('one spec:\ngithub.com/truewire-dev/bluesky')
    assert facet['features'][0]['uri'] == 'https://github.com/truewire-dev/bluesky'

  def test_a_bare_domain_gains_a_scheme_but_the_text_keeps_none(self):
    """The `uri` must be absolute; the covered text is what the reader sees."""
    text = 'truewire.dev'
    [facet] = links(text)
    assert facet['features'][0]['uri'] == 'https://truewire.dev'
    assert sliced(text, facet) == 'truewire.dev'

  def test_an_explicit_scheme_is_left_alone(self):
    [facet] = links('see https://truewire.dev/docs for more')
    assert facet['features'][0]['uri'] == 'https://truewire.dev/docs'

  def test_two_links_are_two_facets(self):
    assert len(links('truewire.dev and github.com/truewire-dev/bluesky')) == 2


class TestTheByteOffsetTrap:
  """The whole reason this is a tested module."""

  def test_a_multibyte_character_before_the_url_shifts_the_offsets(self):
    """An em dash is three bytes and one character. A client using character indices puts
    the facet two bytes early, which silently mangles the link rather than failing."""
    text = 'the fix — github.com/truewire-dev/bluesky'
    [facet] = links(text)
    assert sliced(text, facet) == 'github.com/truewire-dev/bluesky'
    assert facet['index']['byteStart'] != text.index('github.com'), (
      'this test is worthless unless the byte and character offsets actually differ'
    )

  def test_an_arrow_and_an_emoji_too(self):
    text = '→ 400 🎉 truewire.dev'
    [facet] = links(text)
    assert sliced(text, facet) == 'truewire.dev'

  def test_offsets_survive_a_multibyte_character_inside_an_earlier_line(self):
    text = 'line one — with a dash\nline two\ntruewire.dev'
    [facet] = links(text)
    assert sliced(text, facet) == 'truewire.dev'

  def test_every_facet_of_a_multi_link_post_lands_correctly(self):
    text = 'first — truewire.dev, then — github.com/truewire-dev/bluesky'
    found = links(text)
    assert [sliced(text, facet) for facet in found] == [
      'truewire.dev',
      'github.com/truewire-dev/bluesky',
    ]


class TestWhatIsNotALink:
  def test_sentence_punctuation_is_not_part_of_the_url(self):
    text = 'read truewire.dev.'
    [facet] = links(text)
    assert sliced(text, facet) == 'truewire.dev'

  def test_a_closing_bracket_is_not_part_of_the_url(self):
    text = '(see truewire.dev)'
    [facet] = links(text)
    assert sliced(text, facet) == 'truewire.dev'

  def test_an_email_address_is_not_linked_as_its_domain(self):
    """`hello@truewire.dev` must not produce a link to `truewire.dev`."""
    assert links('write to hello@truewire.dev') == []

  def test_an_abbreviation_is_not_a_link(self):
    """A bare-domain matcher that took any dotted word would link `e.g` and `Node.js`.
    Conservative on purpose: a missed link is a nuisance, an invented one is a bug."""
    assert links('e.g. a client, or Node.js, or version 1.2') == []


class TestThePostRecord:
  def test_a_record_without_links_carries_no_facets_key(self):
    """An empty `facets` is accepted and says nothing, so it is omitted."""
    record = post('no links here', created_at='2026-09-09T18:00:00Z')
    assert 'facets' not in record
    assert record['$type'] == 'app.bsky.feed.post'

  def test_a_record_with_a_link_carries_it(self):
    record = post('one spec: github.com/truewire-dev/bluesky', created_at='2026-09-09T18:00:00Z')
    assert record['facets'][0]['features'][0]['uri'].endswith('/truewire-dev/bluesky')

  def test_langs_are_included_when_given_and_omitted_when_not(self):
    assert post('x', created_at='2026-09-09T18:00:00Z', langs=['en'])['langs'] == ['en']
    assert 'langs' not in post('x', created_at='2026-09-09T18:00:00Z')
