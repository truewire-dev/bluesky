"""What the Jetstream recording proves, replayed through the mock server.

One test per subscription example. Jetstream pushes from the moment the socket opens, so
the mock replays the recorded events on connect and the client reads exactly as many as
were recorded; asking for one more would wait for a firehose that is not there.

A subscription whose events have not been recorded yet skips with the reason, the same
way `test/test_recordings.py` skips an unrecorded HTTP example.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
from truewire.examples import (
  client_identifier,
  coerce_ws_example_call,
  resolve_endpoint_function,
)
from truewire.spec.repo import endpoint_records, load_ws_parameters_example
from typing_extensions import Any

PROJECT = Path(__file__).resolve().parents[3]
SPEC = PROJECT / 'spec'

KINDS = {'commit', 'identity', 'account'}


def new_posts(events: list[Any]) -> None:
  """`wantedCollections=['app.bsky.feed.post']` narrows commits to the post collection."""
  for event in events:
    assert event['kind'] in KINDS
    assert event['did'].startswith('did:')
    assert isinstance(event['time_us'], datetime), 'epoch-micros parses into a datetime'
    assert event['time_us'].tzinfo is not None
    if event['kind'] == 'commit':
      commit = event['commit']
      assert commit['collection'] == 'app.bsky.feed.post'
      assert commit['operation'] in ('create', 'update', 'delete')
      if commit['operation'] == 'delete':
        assert 'record' not in commit
      else:
        assert commit['record']['$type'] == 'app.bsky.feed.post'


PROVES = {'jetstream.events[posts]': new_posts}


def subscription_examples() -> list[Any]:
  """Every recorded subscription's parameters, recorded events or not, as a pytest param."""
  params = []
  for record in endpoint_records(PROJECT):
    if record.endpoint.spec.kind != 'stream':
      continue
    function = record.endpoint.resolved_function(record.path, SPEC)
    for parameters in sorted((record.path.parent / 'examples').glob('*.parameters.json')):
      example_id = parameters.name.removesuffix('.parameters.json')
      params.append(pytest.param(record, parameters, id=f'{function}[{example_id}]'))
  return params


def test_every_subscription_has_an_assertion():
  """A new subscription example needs a `PROVES` entry, or its recording proves nothing."""
  ids = {param.id for param in subscription_examples()}
  assert ids == set(PROVES)


@pytest.mark.asyncio
@pytest.mark.parametrize('record,parameters_file', subscription_examples())
async def test_subscription(client, record, parameters_file, request):
  messages_file = parameters_file.with_name(
    parameters_file.name.replace('.parameters.json', '.messages.json')
  )
  if not messages_file.exists():
    pytest.skip(
      f'{request.node.callspec.id}: no response recorded yet '
      '(no Jetstream events captured); run test/recapture.sh'
    )
  expected = json.loads(messages_file.read_text())
  assert expected, 'a recorded subscription carries at least one event'

  async with client:
    subscribe = resolve_endpoint_function(
      client, record.endpoint, endpoint_path=record.path, spec_root=SPEC
    )
    args, kwargs = coerce_ws_example_call(
      subscribe, load_ws_parameters_example(parameters_file), identifier=client_identifier(PROJECT)
    )
    stream = await subscribe(*args, **kwargs)
    try:
      events = []
      pushed = stream.__aiter__()
      for _ in expected:
        events.append(await asyncio.wait_for(anext(pushed), timeout=10))
    finally:
      await stream.unsubscribe()

  assert len(events) == len(expected)
  PROVES[request.node.callspec.id](events)
