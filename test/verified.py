"""Keep the `unverified` declarations in step with the recordings.

`truewire capture` writes a pair but leaves the endpoint's declaration, and `truewire
examples` then fails on the stale block; `test/recapture.sh` runs this last to drop the
declaration of every endpoint that now has a recorded pair. `--check` lists the
endpoints still without one and fails when there are any: CI's strict gate.

An endpoint's pair is a request beside a response for an HTTP endpoint, and the
subscription's parameters beside the events it pushed for the Jetstream stream.

One reason to have no pair survives the strict gate: an endpoint that declares
`unverified.reason: "missing_credentials"` is one the project cannot record at all, not
one nobody has got round to recording. `feed.search_posts` is the only such endpoint here
— the public AppView answers it 403 without a session — and the gate would otherwise be a
line CI can never reach.
"""

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

PAIRS = (('.request.json', '.response.json'), ('.parameters.json', '.messages.json'))
"""The two file conventions a recorded pair takes: an HTTP call, and a subscription."""

EXEMPT = 'missing_credentials'
"""The one `unverified.reason` the strict gate accepts in place of a recording."""


def exempt(doc: dict) -> bool:
  """Whether this endpoint is one the project has no way of recording."""
  return doc.get('unverified', {}).get('reason') == EXEMPT


def recorded(endpoint: Path) -> bool:
  examples = endpoint.parent / 'examples'
  return any(
    call.with_name(call.name.replace(asked, answered)).exists()
    for asked, answered in PAIRS
    for call in examples.glob(f'*{asked}')
  )


def drop_stale() -> int:
  dropped = 0
  for endpoint in sorted((PROJECT / 'spec' / 'endpoints').rglob('endpoint.json')):
    doc = json.loads(endpoint.read_text())
    if recorded(endpoint) and 'unverified' in doc:
      del doc['unverified']
      endpoint.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n')
      print(f'{endpoint.relative_to(PROJECT)}: recorded, dropped `unverified`')
      dropped += 1
  print(f'{dropped} declaration(s) dropped')
  return 0


def check() -> int:
  missing, excused = [], []
  for endpoint in sorted((PROJECT / 'spec' / 'endpoints').rglob('endpoint.json')):
    if recorded(endpoint):
      continue
    path = endpoint.parent.relative_to(PROJECT / 'spec' / 'endpoints')
    (excused if exempt(json.loads(endpoint.read_text())) else missing).append(path)
  for path in excused:
    print(f'{path}: no recording, and declares it needs a credential to get one')
  for path in missing:
    print(f'{path}: no recording; run test/recapture.sh')
  print(f'{len(missing)} endpoint(s) without a recording, {len(excused)} excused')
  return 1 if missing else 0


if __name__ == '__main__':
  sys.exit(check() if '--check' in sys.argv[1:] else drop_stale())
