/**
 * Hand-written core for the Bluesky client: the AppView's HTTP transport, the XRPC error
 * mapping, and the Jetstream socket.
 *
 * Nothing here is generated, and regenerating the client never touches it. The generated
 * `Bluesky` class asks for a `BlueskyCore` -- `{ client, socket }` -- and satisfies it
 * structurally, so `newBluesky` below is the only place the two halves meet.
 */
import { Bluesky, type BlueskyCore } from '../main.js'
import { Transport, type TransportOptions } from './http.js'
import { JetstreamSocket, type JetstreamOptions } from './jetstream.js'

export { BSKY_SOCIAL, PUBLIC_APPVIEW, raiseForStatus, Transport, type TransportOptions } from './http.js'
export { JETSTREAM, JetstreamSocket, type JetstreamOptions } from './jetstream.js'

export interface BlueskyOptions extends TransportOptions {
  /** The Jetstream instance to subscribe to; a `truewire mock` address in tests. */
  wsUrl?: string
}

/** A client and the two transports behind it, ready to call and to subscribe. */
export interface Client extends AsyncDisposable {
  readonly bluesky: Bluesky
  readonly core: BlueskyCore & { client: Transport; socket: JetstreamSocket }
}

/**
 * Build a client.
 *
 * `validate` is the default for both transports: responses and pushed events are checked
 * against their declared types unless a call passes its own `validate`.
 */
export function newBluesky(options: BlueskyOptions = {}): Client {
  const { wsUrl, ...transport } = options
  const socketOptions: JetstreamOptions = { url: wsUrl, validate: options.validate }
  const core = { client: new Transport(transport), socket: new JetstreamSocket(socketOptions) }
  return {
    bluesky: new Bluesky(core),
    core,
    async [Symbol.asyncDispose]() {
      await core.socket.close()
    },
  }
}
