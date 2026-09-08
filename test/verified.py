"""Keep the `unverified` declarations in step with the recordings.

`truewire capture` writes a pair but leaves the endpoint's declaration, and `truewire
examples` then fails on the stale block; `test/recapture.sh` runs this last to drop the
declaration of every endpoint that now has a recorded pair. `--check` lists the
endpoints still without one and fails when there are any: CI's strict gate.

An endpoint's pair is a request beside a response for an HTTP endpoint, and the
subscription's parameters beside the events it pushed for the Jetstream stream.
"""

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

PAIRS = (('.request.json', '.response.json'), ('.parameters.json', '.messages.json'))
"""The two file conventions a recorded pair takes: an HTTP call, and a subscription."""


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
  missing = [
    endpoint.parent.relative_to(PROJECT / 'spec' / 'endpoints')
    for endpoint in sorted((PROJECT / 'spec' / 'endpoints').rglob('endpoint.json'))
    if not recorded(endpoint)
  ]
  for path in missing:
    print(f'{path}: no recording; run test/recapture.sh')
  print(f'{len(missing)} endpoint(s) without a recording')
  return 1 if missing else 0


if __name__ == '__main__':
  sys.exit(check() if '--check' in sys.argv[1:] else drop_stale())
