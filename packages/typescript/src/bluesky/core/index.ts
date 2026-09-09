/**
 * The package's entry point: the hand-written half in front, the generated half behind it.
 *
 * A caller imports `Bluesky` from here and gets the subclass with the factory and the
 * disposal on it; the generated types, codecs and `meta` are re-exported unchanged, so the
 * whole surface is one import. Nothing here is generated, and regenerating never touches
 * it.
 */
export { Bluesky, type BlueskyOptions, type Transports } from './client.js'
export { BSKY_SOCIAL, PUBLIC_APPVIEW, raiseForStatus, Transport, type TransportOptions } from './http.js'
export { JETSTREAM, JetstreamSocket, type JetstreamOptions } from './jetstream.js'

export type { BlueskyCore } from '../main.js'
export * from '../types/index.js'
export * from '../meta.js'
export type { CallOptions } from '@truewire/core'
