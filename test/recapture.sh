#!/usr/bin/env sh
# Re-record every example in this project from the live Bluesky APIs with `truewire
# capture` and, for Jetstream, `test/record_jetstream.py`. Run from anywhere; needs
# `truewire` and the package on the path (`pip install -e '.[dev]'`).
#
# Almost no credentials: the public AppView at public.api.bsky.app answers every endpoint
# here but search, and Jetstream is open to anyone. An access JWT would otherwise only add
# the viewer's own state to a profile or a post, which is not what these examples record.
# An endpoint that declares `unverified.reason: "missing_credentials"` is skipped rather
# than attempted, so a documented gap does not read as a failure every run.
#
# The request half of each example (`examples/<id>.request.json`) and the parameters of
# each subscription (`examples/<id>.parameters.json`) are the source of truth: this
# script replays exactly those, so re-recording keeps the same ids and descriptions and
# only the responses move. Endpoints that gain a recording lose their `unverified`
# declaration at the end (`test/verified.py`).
#
# One example failing does not stop the rest. The first run of this script died on a
# deleted post and recorded nothing, though eleven other endpoints had answered
# perfectly well; a recording run should bank what the API gave it and report the rest,
# because a live API will always have one endpoint having a bad day. Failures are listed
# at the end and the exit code is non-zero, so a workflow can open the pull request for
# what did record and still show red.
cd "$(dirname "$0")/.." || exit 1
PYTHON=${PYTHON:-python3}
JETSTREAM_SECONDS=${JETSTREAM_SECONDS:-10}
JETSTREAM_LIMIT=${JETSTREAM_LIMIT:-20}

failed=''
recorded=0

# Two examples pin one post by AT URI, and a post can be deleted. That is repaired before
# anything is captured rather than after a capture failed, because only one of the two
# fails: `getPosts` answers 200 with an empty array for a post that is gone, and an empty
# array records as happily as a full one.
if ! "$PYTHON" test/refresh_post_examples.py; then
  failed="$failed test/refresh_post_examples.py"
fi

needs_credentials() {
  "$PYTHON" -c 'import json, sys
doc = json.load(open(sys.argv[1]))
sys.exit(0 if doc.get("unverified", {}).get("reason") == "missing_credentials" else 1)' "$1"
}

capture_example() {
  request=$1
  function=$(echo "$request" | awk -F/ '{print $3 "." $4}')
  id=$(basename "$request" .request.json)
  description=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("description", ""))' "$request")
  parameters=$("$PYTHON" -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["request"]))' "$request")
  truewire capture "$function" --id "$id" -d "$description" --request "$parameters"
}

skipped=''
for request in spec/endpoints/*/*/examples/*.request.json; do
  endpoint=$(dirname "$(dirname "$request")")/endpoint.json
  if needs_credentials "$endpoint"; then
    skipped="$skipped $request"
    continue
  fi
  if capture_example "$request"; then
    recorded=$((recorded + 1))
    continue
  fi
  failed="$failed $request"
done

# `truewire capture` refuses a stream endpoint, so the firehose is read directly through
# the generated client for a short window and the events it pushed are written beside the
# subscription's parameters.
if "$PYTHON" test/record_jetstream.py --seconds "$JETSTREAM_SECONDS" --limit "$JETSTREAM_LIMIT"; then
  recorded=$((recorded + 1))
else
  failed="$failed jetstream"
fi

"$PYTHON" test/verified.py

echo
echo "recorded $recorded example(s)"
if [ -n "$skipped" ]; then
  echo 'these need a credential this project does not carry, and say so in their endpoint:'
  for request in $skipped; do echo "  $request"; done
fi
if [ -n "$failed" ]; then
  echo 'these did not record, and their endpoints keep saying why they have none:'
  for request in $failed; do echo "  $request"; done
  exit 1
fi
