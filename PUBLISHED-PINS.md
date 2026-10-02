# Published dependency validation

TRU-916 updates the published CLI/runtime dependencies from source `fbcf4815545eb2908774a0086199f97a72b8cb26`.
Python runtime floor: 0.3.0 (below 0.4); CLI: 0.11.0; npm and Cargo runtime floors: 0.2.0.
Yarn/Cargo lockfiles and `published-constraints.txt` select the exact release identities.
Generated source is unchanged.

Run from any checkout (uv, Python 3.12, Node, Yarn 1 and Cargo required):

```sh
python3 tools/check-published.py . /absolute/path/to/new-ticket-scratch
```

The destination must not exist. The script archives tracked HEAD, creates a fresh venv,
installs the Python client with development dependencies from PyPI, installs npm packages
with the frozen Yarn lockfile, and fetches the locked Rust graph into a fresh Cargo home.
Each command is bounded to 300 seconds. It asserts actual installed package versions and
registry origins, prints each install command and source commit, and retains identities.json
and the clean source tree for serial test/lint/score execution with CARGO_BUILD_JOBS=1.
It prints `showcase published pins verified` only after all four identities match.
This validates dependency resolution; it does not claim client tests or B1 pass.

Use the retained source/.venv/bin on PATH for subsequent tests, set CARGO_HOME to the
retained cargo-home, and remove PYTHONPATH and inherited runtime overrides. The source
archive excludes local credentials and build environments. No live calls are required.
