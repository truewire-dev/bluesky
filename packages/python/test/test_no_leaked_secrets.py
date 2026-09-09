"""No recorded example contains a credential.

The write half of this client authenticates, and its first endpoint answers with two live
tokens and an email address. Everything about the design is meant to keep those out of the
repository -- the credentials are injected by the transport rather than passed as
parameters, so `truewire capture` (which writes the request half from the parameters it was
given, never from the wire body) has nothing to write, and the response is scrubbed on
capture.

That is a chain of four claims, and this file exists because a chain of four claims is
exactly the kind of thing that holds until it doesn't. `truewire capture` shipped a bug in
0.8.2 that wrote an access token into a committed example, on a client where nobody was
looking for one. So this looks at what is actually on disk, and fails the build rather than
warning.

`truewire standards` has its own S16 heuristic over credential-shaped field names. That one
is a warning by design and tuned for a wide corpus; this one is narrow, specific to this
client's real secrets, and fatal.
"""

import json
import os
import re
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[3]
SPEC = PROJECT / 'spec'

RECORDED = sorted(SPEC.rglob('*.json'))

JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}')
"""A JSON Web Token on the wire: three base64url segments, the first of which is a JSON
header and so always begins `eyJ`. Both of Bluesky's session tokens are shaped this way."""

EMAIL_KEYS = frozenset({'email'})
"""Field names whose value is an account's own address. `createSession` answers with one.

Deliberately a field check rather than a search for anything email-shaped. The first
version of this test searched the text and failed on four recordings, every one of them a
public address inside somebody's profile bio (`support@bsky.app`). A gate that fires on
legitimate content is a gate somebody switches off, and the risk here was never "an email
appears in a payload" -- profiles are full of them -- but "this account's own address was
captured from a session frame"."""


def fields(value: object, key: str | None = None):
  """Every (key, value) pair in a decoded document, at any depth."""
  if isinstance(value, dict):
    for name, item in value.items():
      yield from fields(item, name)
  elif isinstance(value, list):
    for item in value:
      yield from fields(item, key)
  elif key is not None:
    yield key, value


def spec_files() -> list[Path]:
  return RECORDED


def test_there_are_recordings_to_check():
  """A guard on the guard. Every assertion below passes vacuously over an empty corpus, and
  an empty corpus is exactly what a broken glob produces."""
  responses = [path for path in spec_files() if path.name.endswith('.response.json')]
  assert len(responses) >= 10, f'only found {len(responses)} recorded responses; check the glob'


@pytest.mark.parametrize('path', spec_files(), ids=lambda p: str(p.relative_to(PROJECT)))
def test_no_json_web_token_in_the_spec_tree(path: Path):
  """No file under `spec/` carries anything JWT-shaped.

  This is the one that matters. A leaked access token is short-lived; a leaked refresh
  token is not, and git history is forever.
  """
  found = JWT.findall(path.read_text())
  assert not found, (
    f'{path.relative_to(PROJECT)} contains {len(found)} JWT-shaped string(s). '
    'Re-record with `--scrub accessJwt --scrub refreshJwt`, and rotate the app password: '
    'anything that reached a commit must be treated as public.'
  )


@pytest.mark.parametrize(
  'path',
  [p for p in spec_files() if p.name.endswith('.response.json')],
  ids=lambda p: str(p.relative_to(PROJECT)),
)
def test_no_captured_email_field_holds_a_real_address(path: Path):
  """An `email` field in a recorded payload holds a placeholder, not an address.

  Scoped to captured responses and to the field itself: an address inside a profile
  description is public content this client is supposed to return.
  """
  payload = json.loads(path.read_text()).get('payload')
  real = [
    value
    for key, value in fields(payload)
    if key in EMAIL_KEYS
    and isinstance(value, str)
    and '@' in value
    and 'REDACTED' not in value.upper()
  ]
  assert not real, (
    f'{path.relative_to(PROJECT)} captured a real address in an `email` field. '
    'Re-record with `--scrub email`.'
  )


@pytest.mark.skipif(
  not os.environ.get('BLUESKY_APP_PASSWORD'),
  reason='no app password in the environment to search for',
)
@pytest.mark.parametrize('path', spec_files(), ids=lambda p: str(p.relative_to(PROJECT)))
def test_the_app_password_itself_appears_nowhere(path: Path):
  """The literal credential, wherever one is configured.

  Only runs where a credential is present -- a recording run, or a developer's machine --
  which is exactly where a leak would be created. The assertion never prints the value it
  is searching for, including on failure.
  """
  password = os.environ['BLUESKY_APP_PASSWORD']
  assert password not in path.read_text(), (
    f'{path.relative_to(PROJECT)} contains the configured app password verbatim. '
    'Revoke it now at bsky.app > Settings > App Passwords, then find out how it got there: '
    'no endpoint declares it as a parameter, so this should not be reachable.'
  )


def test_the_session_recording_is_scrubbed_rather_than_absent():
  """`create_session` records the shape of a session, with the values replaced.

  A recording that simply omitted the tokens would pass every check above and prove
  nothing about the endpoint. This asserts the opposite: the fields are present, and their
  values are placeholders.
  """
  examples = sorted((SPEC / 'endpoints/server/create_session/examples').glob('*.response.json'))
  if not examples:
    pytest.skip('create_session has no recording yet')
  for example in examples:
    payload = json.loads(example.read_text())['payload']
    for field in ('accessJwt', 'refreshJwt'):
      assert field in payload, f'{example.name} lost `{field}`; the shape is the point'
      assert 'REDACTED' in str(payload[field]).upper(), (
        f'{example.name} carries a real-looking `{field}`'
      )
