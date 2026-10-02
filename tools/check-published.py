#!/usr/bin/env python3
"""Clean-install one standalone showcase HEAD and assert registry runtime identities.

Usage: python3 check-published.py CHECKOUT NEW_SCRATCH_DIRECTORY
Requires Python 3.11+, uv, Yarn 1, Node and Cargo. Each command has a 300s bound.
The new directory is retained for serial tests/score and evidence inspection.
"""

import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile


def main():
    checkout, destination = map(lambda p: Path(p).resolve(), sys.argv[1:])
    destination.mkdir(parents=True, exist_ok=False)
    source = destination / "source"
    source.mkdir()
    # Only tracked HEAD content; no local .venv, node_modules, Cargo config or secrets.
    archive = subprocess.check_output(["git", "-C", str(checkout), "archive", "HEAD"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(source, filter="data")
    revision = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    print(f"source: {checkout} @ {revision}", flush=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(
        ("UV_", "PIP_", "PYTHON", "CARGO_", "RUSTFLAGS", "RUSTDOCFLAGS", "NODE_", "NPM_", "npm_", "YARN_", "VIRTUAL_ENV")
    )}
    env.update(UV_NO_CONFIG="1", CARGO_HOME=str(destination / "cargo-home"),
               CARGO_BUILD_JOBS="1", npm_config_userconfig="/dev/null")
    env["PATH"] = str(source / ".venv/bin") + os.pathsep + env["PATH"]

    def run(args, cwd=source, capture=False):
        print(f"[{cwd.relative_to(destination)}] $ {shlex.join(map(str, args))}", flush=True)
        return subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True,
                              text=True, stdout=subprocess.PIPE if capture else None,
                              timeout=300).stdout

    manifests = [source / "packages/python/pyproject.toml",
                 source / "packages/typescript/package.json", source / "packages/rust/Cargo.toml"]
    for manifest in manifests:
        if not manifest.is_file():
            raise SystemExit(f"Missing delivery package: {manifest.relative_to(source)}")
    for pattern in ("**/.cargo/config*", "**/.npmrc", "**/.yarnrc*"):
        if list(source.glob(pattern)):
            raise SystemExit(f"Review local override configuration before validation: {pattern}")
    run(["uv", "venv", "--python", "3.12", ".venv"])
    run(["uv", "pip", "install", "--python", ".venv/bin/python", "--index-url",
         "https://pypi.org/simple", "-c", "published-constraints.txt", "./packages/python[dev]"])
    py = json.loads(run([source / ".venv/bin/python", "-I", "-c", """
import importlib.metadata as m, json
result = {}
for name in ('truewire', 'truewire-core'):
    d = m.distribution(name)
    assert d.read_text('direct_url.json') is None, (name, 'non-registry install')
    result[name] = {'version': d.version, 'location': str(d.locate_file(''))}
print(json.dumps(result))
"""], capture=True))
    assert py["truewire"]["version"] == "0.11.0", py
    assert py["truewire-core"]["version"] == "0.3.0", py
    ts_dir = source / "packages/typescript"
    run(["yarn", "--no-default-rc", "install", "--frozen-lockfile", "--non-interactive",
         "--ignore-scripts", "--registry", "https://registry.npmjs.org"], cwd=ts_dir)
    ts = json.loads(run(["node", "--input-type=module", "-e", r"""
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
const root = fs.realpathSync('node_modules/@truewire/core');
if (root !== process.cwd() + '/node_modules/@truewire/core') throw Error('local override');
const pkg = JSON.parse(fs.readFileSync(root + '/package.json', 'utf8'));
if (pkg.name !== '@truewire/core' || pkg.version !== '0.2.0') throw Error('wrong package');
const integrity = JSON.parse(fs.readFileSync('node_modules/.yarn-integrity', 'utf8'));
const origins = Object.entries(integrity.lockfileEntries)
  .filter(([name]) => name.startsWith('@truewire/core@')).map(([, url]) => url);
if (origins.length !== 1 || !/^https:\/\/registry\.(yarnpkg\.com|npmjs\.org)\/@truewire\/core\/-\/core-0\.2\.0\.tgz#[a-f0-9]+$/.test(origins[0]))
  throw Error('non-registry runtime origin: ' + origins);
const entry = fileURLToPath(import.meta.resolve('@truewire/core'));
if (!entry.startsWith(root + '/')) throw Error('foreign entry');
await import('@truewire/core');
console.log(JSON.stringify({name: pkg.name, version: pkg.version, entry, origin: origins[0]}));
"""], cwd=ts_dir, capture=True))
    metadata = json.loads(run(["cargo", "metadata", "--locked", "--format-version", "1"],
                              cwd=source / "packages/rust", capture=True))
    rust = [p for p in metadata["packages"] if p["name"] == "truewire-core"]
    assert len(rust) == 1 and rust[0]["version"] == "0.2.0", rust
    assert rust[0]["source"] == "registry+https://github.com/rust-lang/crates.io-index", rust
    assert Path(rust[0]["manifest_path"]).is_relative_to(destination / "cargo-home/registry/src"), rust
    evidence = {"revision": revision, "python": py, "typescript": ts,
                "rust": {k: rust[0][k] for k in ("name", "version", "source", "manifest_path")}}
    (destination / "identities.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    print("showcase published pins verified", flush=True)


if __name__ == "__main__":
    main()
