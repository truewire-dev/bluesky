/**
 * The Jetstream transport: one WebSocket per subscription, and nothing sent on it.
 *
 * Jetstream has no subscribe frame. A consumer opens
 * `wss://.../subscribe?wantedCollections=...&cursor=...` and the server pushes from the
 * moment the socket is accepted; closing the socket is what unsubscribes. So the
 * subscription's parameters become the connection URL's query string, the `channel` the
 * generated endpoint passes names the subscription only locally, and `unsubscribe()`
 * closes the connection.
 *
 * That is why each subscription owns a connection rather than sharing one: two
 * subscriptions want different query strings, and a query string is fixed at connect
 * time.
 */
import { ws, Stream, Subscription, type StreamEndpoint, type SubscribeCall } from '@truewire/core'

/**
 * One of the public Jetstream instances. The others (`jetstream1.us-east`,
 * `jetstream1.us-west`, `jetstream2.us-west`) serve the same stream.
 */
export const JETSTREAM = 'wss://jetstream2.us-east.bsky.network/subscribe'

export interface JetstreamOptions extends Omit<ws.SocketOptions, 'url'> {
  /** The Jetstream instance to subscribe to; a `truewire mock` address in tests. */
  url?: string
  /** Validate pushed events by default; a call's own `validate` option overrides it. */
  validate?: boolean
}

/** One connection, pushing every frame it is given into a queue for the iterator. */
class Connection extends ws.Socket {
  readonly events = new ws.AsyncQueue<unknown>()

  onMsg(msg: ws.Data): void {
    const text = typeof msg === 'string' ? msg : new TextDecoder().decode(msg)
    this.events.push(JSON.parse(text))
  }
}

/** The transport the generated Jetstream endpoint calls: `StreamEndpoint` by shape. */
export class JetstreamSocket implements StreamEndpoint {
  readonly url: string
  readonly validate: boolean
  private readonly options: Omit<ws.SocketOptions, 'url'>
  private readonly open = new Set<Connection>()

  constructor(options: JetstreamOptions = {}) {
    const { url, validate, ...rest } = options
    this.url = url ?? JETSTREAM
    this.validate = validate ?? true
    this.options = rest
  }

  /**
   * Subscribe with `call.parameters`; each pushed event is validated unless `validate`
   * is off. Nothing connects until the subscription is awaited or iterated.
   */
  subscribe<Params, Message>(call: SubscribeCall<Params, Message, Record<string, never>>): Subscription<Message> {
    const values =
      call.parameters !== undefined && call.parametersCodec !== undefined
        ? (call.parametersCodec.dump(call.parameters) as Record<string, unknown>)
        : {}
    const codec = call.messageCodec
    const validate = (call.validate ?? this.validate) && codec !== undefined
    return new Subscription<Message>(async () => {
      const conn = new Connection({ ...this.options, url: `${this.url}${query(values)}` })
      this.open.add(conn)
      const close = async (): Promise<undefined> => {
        this.open.delete(conn)
        await conn.close()
        return undefined
      }
      const events = async function* (): AsyncGenerator<Message> {
        try {
          for (;;) {
            const event = await conn.wait(conn.events.pull())
            yield (validate ? codec!.parse(event) : event) as Message
          }
        } finally {
          await close()
        }
      }
      // Connect before the first pull so that a server that refuses the parameters fails
      // here, where the caller is awaiting, rather than on the first iteration.
      await conn.open()
      return new Stream<Message>(undefined, events(), close)
    })
  }

  /** Close every connection this client still holds open. */
  async close(): Promise<void> {
    const open = [...this.open]
    this.open.clear()
    await Promise.all(open.map(conn => conn.close()))
  }
}

/** The subscription's parameters as a query string, a list-valued one as repeated keys. */
function query(values: Record<string, unknown>): string {
  const params = new URLSearchParams()
  for (const [name, value] of Object.entries(values)) {
    if (value === null || value === undefined) continue
    for (const item of Array.isArray(value) ? value : [value]) params.append(name, String(item))
  }
  const rendered = params.toString()
  return rendered === '' ? '' : `?${rendered}`
}
