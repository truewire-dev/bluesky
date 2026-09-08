#!/usr/bin/env sh
# Re-record every example in this project from the live Bluesky APIs with `truewire
# capture` and, for Jetstream, `test/record_jetstream.py`. Run from anywhere; needs
# `truewire` and the package on the path (`pip install -e '.[dev]'`).
#
# No credentials: every endpoint here is answered by the public AppView at
# public.api.bsky.app, and Jetstream is open to anyone. An access JWT would only add the
# viewer's own state to a profile or a post, which is not what these examples record.
#
# The request half of each example (`examples/<id>.request.json`) and the parameters of
# each subscription (`examples/<id>.parameters.json`) are the source of truth: this
# script replays exactly those, so re-recording keeps the same ids and descriptions and
# only the responses move. Endpoints that gain a recording lose their `unverified`
# declaration at the end (`test/verified.py`).
set -e
cd "$(dirname "$0")/.."
PYTHON=${PYTHON:-python3}
JETSTREAM_SECONDS=${JETSTREAM_SECONDS:-10}
JETSTREAM_LIMIT=${JETSTREAM_LIMIT:-20}

for request in spec/endpoints/*/*/examples/*.request.json; do
  function=$(echo "$request" | awk -F/ '{print $3 "." $4}')
  id=$(basename "$request" .request.json)
  description=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("description", ""))' "$request")
  parameters=$("$PYTHON" -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["request"]))' "$request")
  truewire capture "$function" --id "$id" -d "$description" --request "$parameters"
done

# `truewire capture` refuses a stream endpoint, so the firehose is read directly through
# the generated client for a short window and the events it pushed are written beside the
# subscription's parameters.
"$PYTHON" test/record_jetstream.py --seconds "$JETSTREAM_SECONDS" --limit "$JETSTREAM_LIMIT"

"$PYTHON" test/verified.py
