"""Record real Jetstream events for every subscription example.

`truewire capture` records HTTP request/reply pairs only. Asked for the stream it stops
before opening anything:

    jetstream.events is not an HTTP rpc endpoint; capture records HTTP request/reply pairs only

So the stream is recorded here instead, through the same generated client and the same
recorded parameters: subscribe with `examples/<id>.parameters.json`, read for a bounded
window, and write what the server pushed to `examples/<id>.messages.json`, the file
`truewire check` validates and `truewire mock` replays.

The client is built with `validate=False` on purpose: a recording is the wire body the
client saw, so `time_us` stays the integer Jetstream sent rather than the `datetime` a
validated event carries. `truewire check` validates the file afterwards.

Run it from `test/recapture.sh`, or on its own with network access:

    python test/record_jetstream.py --seconds 10 --limit 20
"""

import argparse
import asyncio
import json
from pathlib import Path

from truewire.examples import (
  client_identifier,
  coerce_ws_example_call,
  resolve_endpoint_function,
)
from truewire.spec.repo import EndpointRecord, endpoint_records, load_ws_parameters_example
from typing_extensions import Any

from bluesky import Bluesky

PROJECT = Path(__file__).resolve().parents[1]
SPEC = PROJECT / 'spec'


async def collect(
  client: Bluesky, record: EndpointRecord, parameters_file: Path, *, seconds: float, limit: int
) -> list[Any]:
  """Subscribe with one example's parameters and return the events of `seconds`.

  Stops at `limit` events or when the window closes, whichever comes first. Jetstream
  never ends a subscription of its own accord, so the window is what ends this one.
  """
  example = load_ws_parameters_example(parameters_file)
  subscribe = resolve_endpoint_function(
    client, record.endpoint, endpoint_path=record.path, spec_root=SPEC
  )
  args, kwargs = coerce_ws_example_call(subscribe, example, identifier=client_identifier(PROJECT))
  events: list[Any] = []
  stream = await subscribe(*args, **kwargs)
  try:

    async def read() -> None:
      async for event in stream:
        events.append(event)
        if len(events) >= limit:
          return

    try:
      await asyncio.wait_for(read(), timeout=seconds)
    except (asyncio.TimeoutError, TimeoutError):
      pass
  finally:
    await stream.unsubscribe()
  return events


async def record_all(*, seconds: float, limit: int, ws_url: str | None) -> int:
  """Record every Jetstream example; return the process exit code."""
  streams = [
    record for record in endpoint_records(PROJECT) if record.endpoint.spec.kind == 'stream'
  ]
  recorded = 0
  for record in streams:
    function = record.endpoint.resolved_function(record.path, SPEC)
    for parameters_file in sorted((record.path.parent / 'examples').glob('*.parameters.json')):
      example_id = parameters_file.name.removesuffix('.parameters.json')
      client = (
        Bluesky.new(validate=False)
        if ws_url is None
        else Bluesky.new(ws_url=ws_url, validate=False)
      )
      async with client:
        events = await collect(client, record, parameters_file, seconds=seconds, limit=limit)
      if not events:
        print(f'{function}[{example_id}]: no events in {seconds}s; nothing written')
        continue
      out = parameters_file.with_name(f'{example_id}.messages.json')
      out.write_text(json.dumps(events, indent=2, ensure_ascii=False) + '\n')
      print(f'{function}[{example_id}]: {len(events)} event(s) -> {out.relative_to(PROJECT)}')
      recorded += 1
  if not recorded:
    print('no Jetstream events recorded')
    return 1
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    '--seconds', type=float, default=10.0, help='How long to read each subscription.'
  )
  parser.add_argument(
    '--limit', type=int, default=20, help='How many events to keep per subscription.'
  )
  parser.add_argument(
    '--ws-url', default=None, help='Jetstream instance to subscribe to; omit for the default.'
  )
  args = parser.parse_args()
  return asyncio.run(record_all(seconds=args.seconds, limit=args.limit, ws_url=args.ws_url))


if __name__ == '__main__':
  raise SystemExit(main())
