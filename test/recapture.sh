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
THREAD_EXAMPLE='spec/endpoints/feed/get_post_thread/examples/bsky_app_hello.request.json'

failed=''
recorded=0

capture_example() {
  request=$1
  function=$(echo "$request" | awk -F/ '{print $3 "." $4}')
  id=$(basename "$request" .request.json)
  description=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1])).get("description", ""))' "$request")
  parameters=$("$PYTHON" -c 'import json, sys; print(json.dumps(json.load(open(sys.argv[1]))["request"]))' "$request")
  truewire capture "$function" --id "$id" -d "$description" --request "$parameters"
}

for request in spec/endpoints/*/*/examples/*.request.json; do
  if capture_example "$request"; then
    recorded=$((recorded + 1))
    continue
  fi
  # A thread example names one post, and a post can be deleted. Repair the request half
  # from a post that still exists, then try once more; anything else is a real failure.
  if [ "$request" = "$THREAD_EXAMPLE" ] && "$PYTHON" test/refresh_thread_post.py; then
    if capture_example "$request"; then
      recorded=$((recorded + 1))
      continue
    fi
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
if [ -n "$failed" ]; then
  echo 'these did not record, and their endpoints keep saying why they have none:'
  for request in $failed; do echo "  $request"; done
  exit 1
fi
