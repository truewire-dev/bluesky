/**
 * The client a caller actually constructs: the generated `Bluesky` plus the two things a
 * generated class cannot carry -- a factory that knows the default hosts, and disposal
 * that closes the sockets.
 *
 * Generated TypeScript imports nothing from this package -- it names the contract
 * interfaces and receives an object -- so the two things a generated class cannot carry
 * arrive by subclassing it here rather than by it extending something of ours. Python
 * declares a base in `truewire.toml` instead and has its generated client inherit both;
 * either way a caller writes `Bluesky.new()`. See `NOTES.md` #13.
 */
import { Bluesky as Generated, type BlueskyCore } from '../main.js'
import { Transport, type TransportOptions } from './http.js'
import { JetstreamSocket } from './jetstream.js'

export interface BlueskyOptions extends TransportOptions {
  /** The Jetstream instance to subscribe to; a `truewire mock` address in tests. */
  wsUrl?: string
}

/** The transports behind a client built by `Bluesky.new`. */
export interface Transports extends BlueskyCore {
  client: Transport
  socket: JetstreamSocket
}

/**
 * Bluesky's public API and the Jetstream firehose.
 *
 * ```ts
 * await using client = Bluesky.new()
 * const profile = await client.actor.getProfile({ actor: 'bsky.app' })
 * ```
 */
export class Bluesky extends Generated implements AsyncDisposable {
  declare readonly core: Transports

  /**
   * Build a client. Every option has a default that works.
   *
   * `validate` is the default for both transports: responses and pushed events are checked
   * against their declared types unless a call passes its own `validate`.
   */
  static new(options: BlueskyOptions = {}): Bluesky {
    const { wsUrl, ...transport } = options
    return new Bluesky({
      client: new Transport(transport),
      socket: new JetstreamSocket({ url: wsUrl, validate: options.validate }),
    })
  }

  /**
   * Close every socket this client still holds open.
   *
   * Only subscriptions own anything: the HTTP transport is `fetch`, with no pool to close,
   * so a client that has only made calls disposes of nothing.
   */
  async [Symbol.asyncDispose](): Promise<void> {
    await this.core.socket.close()
  }
}
