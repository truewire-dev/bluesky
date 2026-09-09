"""Shared fixtures: a real `Bluesky` client pointed at the local mock servers.

`base_url` puts the AppView on the mock's HTTP server, the same way it would put it on a
self-hosted XRPC host; `ws_url` puts Jetstream on the mock's WebSocket server. The
WebSocket server only starts once an example has recorded events, so until then `ws_url`
names an address nothing ever connects to.
"""

from pathlib import Path

import pytest
from truewire.mock import running_mock_servers

from bluesky import Bluesky

PROJECT = Path(__file__).resolve().parents[3]

NO_WS_SERVER = 'ws://127.0.0.1:1/subscribe'

PLACEHOLDER_IDENTIFIER = 'example.invalid'
"""A handle in a reserved TLD that can never resolve (RFC 2606)."""

PLACEHOLDER_APP_PASSWORD = 'not-a-real-app-password'
"""Stand-in for the mock's WebSocket address while no Jetstream events are recorded."""


@pytest.fixture
def mock_servers():
  with running_mock_servers(PROJECT) as servers:
    yield servers


@pytest.fixture
def client(mock_servers):
  """The generated client, built exactly as a user would, against the mock's addresses."""
  ws_url = mock_servers.ws_server.url if mock_servers.ws_server is not None else NO_WS_SERVER
  return Bluesky.new(
    base_url=mock_servers.http_base_url,
    ws_url=ws_url,
    validate=True,
    # Obvious placeholders, and deliberately so. The write half needs *a* credential to
    # inject, because injection is what it does; it must never need a real one. The mock
    # ignores both when matching, since `server.create_session` declares them `redacted`
    # (ADR 0007), so these values reach the wire and are then thrown away -- which is
    # exactly the property that keeps a real credential out of the recordings too.
    identifier=PLACEHOLDER_IDENTIFIER,
    app_password=PLACEHOLDER_APP_PASSWORD,
  )
