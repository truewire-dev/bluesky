/**
 * Type-check every TypeScript example in this package's README.
 *
 * `truewire docs check` does this for Python by running pyright over the blocks of
 * `README.md`; there is no TypeScript equivalent yet, so this is it: each ```ts block is
 * written out as its own module and `tsc` compiles the lot against the real package.
 * Nothing runs and nothing is sent, so an example that calls a method the client does not
 * have is a compile error rather than a bug a reader finds for us.
 *
 * Each block therefore has to stand on its own, imports included -- which is how a reader
 * uses one anyway.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { expect, it } from 'vitest'
import { tsRoot } from './setup.js'

const BLOCK = /^```ts\n([\s\S]*?)^```$/gm

it('every TypeScript example in the README compiles against the package', () => {
  const readme = readFileSync(path.join(tsRoot, 'README.md'), 'utf8')
  const blocks = [...readme.matchAll(BLOCK)].map(match => match[1]!)
  expect(blocks.length).toBeGreaterThan(0)

  const dir = path.join(tsRoot, 'test', '.readme')
  rmSync(dir, { recursive: true, force: true })
  mkdirSync(dir, { recursive: true })
  const files = blocks.map((source, index) => {
    const file = path.join(dir, `block-${index + 1}.ts`)
    writeFileSync(file, source)
    return file
  })

  // The examples import the package by the name a consumer would use, which resolves
  // through `exports` to `dist/` -- built on publish, absent in a checkout. So the check
  // maps that name onto the sources, and the README stays written for the reader rather
  // than for the test.
  writeFileSync(path.join(dir, 'tsconfig.json'), JSON.stringify({
    compilerOptions: {
      noEmit: true, strict: true, skipLibCheck: true, target: 'ES2022',
      module: 'nodenext', moduleResolution: 'nodenext',
      lib: ['ES2023', 'ESNext.Disposable'], types: ['node'],
      baseUrl: '../..',
      paths: {
        '@truewire/bluesky': ['src/bluesky/core/index.ts'],
        '@truewire/bluesky/generated': ['src/bluesky/index.ts'],
      },
    },
    files: files.map(file => path.relative(dir, file)),
  }, null, 2))

  try {
    execFileSync(
      path.join(tsRoot, 'node_modules', '.bin', 'tsc'),
      ['-p', path.join(dir, 'tsconfig.json')],
      { cwd: tsRoot, stdio: 'pipe' },
    )
  } catch (e) {
    const { stdout } = e as { stdout?: Buffer }
    throw new Error(`README examples do not compile:\n${stdout?.toString() ?? String(e)}`)
  }
  rmSync(dir, { recursive: true, force: true })
})
