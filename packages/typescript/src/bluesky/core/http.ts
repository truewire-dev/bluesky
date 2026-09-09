/**
 * The AppView's HTTP transport and the XRPC error mapping.
 *
 * Nothing here is generated, and regenerating the client never touches it. Every
 * generated `rpc` endpoint is constructed with a `Transport` and calls `request(...)` on
 * it; the interface is structural (`HttpEndpoint<DefaultMeta>`), so this satisfies the
 * generated client by shape rather than by inheritance.
 *
 * Every endpoint in this client is public: the AppView at `public.api.bsky.app` answers
 * them without credentials. An access JWT (from `com.atproto.server.createSession`, which
 * this client does not wrap) is accepted for calls against `bsky.social`, where the same
 * endpoints also carry the viewer's own state, but nothing here needs one.
 */
import {
  ApiError, AuthError, BadRequest, HttpClient, RateLimited,
  type HttpCall, type HttpEndpoint,
} from '@truewire/core'
import type { DefaultMeta } from '../meta.js'

/** The public AppView: every endpoint here, no credentials, rate-limited by IP. */
export const PUBLIC_APPVIEW = 'https://public.api.bsky.app'

/**
 * Bluesky's own PDS entryway: the same endpoints with an access JWT, plus the
 * authenticated ones this client does not cover.
 */
export const BSKY_SOCIAL = 'https://bsky.social'

/**
 * Map a non-2xx XRPC answer onto the runtime's errors.
 *
 * An XRPC error body is `{"error": "InvalidRequest", "message": "..."}`; both parts are
 * useful, so both go into the message. `400` is a bad request, `401`/`403` an auth
 * failure, `429` the rate limit; everything else is an `ApiError`.
 */
export function raiseForStatus(method: string, path: string, status: number, text: string): never {
  let reason = text.slice(0, 200)
  let body: unknown
  try {
    body = JSON.parse(text)
  } catch {
    body = undefined
  }
  if (body !== null && typeof body === 'object') {
    const { error, message } = body as { error?: unknown; message?: unknown }
    if (typeof error === 'string') reason = typeof message === 'string' ? `${error}: ${message}` : error
  }
  const detail = `${method} ${path}: HTTP ${status}: ${reason}`
  if (status === 400) throw new BadRequest(detail)
  if (status === 401 || status === 403) throw new AuthError(detail)
  if (status === 429) throw new RateLimited(detail)
  throw new ApiError(detail)
}

export interface TransportOptions {
  /**
   * The XRPC host. Omit it for the public AppView, or for `bsky.social` when
   * `accessJwt` is given; a `truewire mock` address in tests.
   */
  baseUrl?: string
  /**
   * An access JWT from `com.atproto.server.createSession`, sent as a bearer token on
   * every call. None of the endpoints here needs one.
   */
  accessJwt?: string
  /** Validate responses by default; a call's own `validate` option overrides it. */
  validate?: boolean
  /** The `fetch` wrapper to send through; one is made when omitted. */
  http?: HttpClient
}

/** The transport every XRPC group calls: `HttpEndpoint<DefaultMeta>` by shape. */
export class Transport implements HttpEndpoint<DefaultMeta> {
  readonly baseUrl: string
  readonly accessJwt: string | undefined
  readonly validate: boolean
  readonly http: HttpClient

  constructor(options: TransportOptions = {}) {
    const fallback = options.accessJwt ? BSKY_SOCIAL : PUBLIC_APPVIEW
    this.baseUrl = (options.baseUrl ?? fallback).replace(/\/+$/, '')
    this.accessJwt = options.accessJwt
    this.validate = options.validate ?? true
    this.http = options.http ?? new HttpClient()
  }

  /** Send one call; the reply's body, validated unless `validate` is off. */
  async request<Req, Res>(call: HttpCall<Req, Res, DefaultMeta>): Promise<Res> {
    const values =
      call.request !== undefined && call.requestCodec !== undefined
        ? (call.requestCodec.dump(call.request) as Record<string, unknown>)
        : {}
    const method = call.method ?? 'GET'
    const { path, query } = fill(call.path, values)
    const response = await this.http.request(method, this.baseUrl + path, {
      query,
      headers: this.headers(call.meta.public === true),
      signal: call.signal,
    })
    const text = await response.text()
    if (response.status >= 400) raiseForStatus(method, path, response.status, text)
    if (call.responseCodec === undefined) return undefined as Res
    const value: unknown = text === '' ? undefined : JSON.parse(text)
    return (call.validate ?? this.validate) ? call.responseCodec.parse(value) : (value as Res)
  }

  /**
   * Headers for one call.
   *
   * The JWT travels whenever the client has one, public endpoint or not: on
   * `bsky.social` it is what fills the viewer state on a profile or a post. A non-public
   * call without one is refused here, before any request is made.
   */
  private headers(isPublic: boolean): Record<string, string> {
    if (this.accessJwt !== undefined) {
      return { Accept: 'application/json', Authorization: `Bearer ${this.accessJwt}` }
    }
    if (!isPublic) {
      throw new AuthError('this endpoint needs an access JWT; pass accessJwt to Bluesky.new()')
    }
    return { Accept: 'application/json' }
  }
}

/**
 * The path with its `{name}` placeholders filled, and the rest as query parameters.
 *
 * XRPC query endpoints take every parameter in the query string, and a list-valued one
 * (`actors`, `uris`) as repeated keys, which is what the AppView reads and what
 * `truewire mock` matches. A value left unset is not sent at all.
 */
function fill(template: string, values: Record<string, unknown>): { path: string; query: URLSearchParams } {
  let path = template
  const query = new URLSearchParams()
  for (const [name, value] of Object.entries(values)) {
    if (value === null || value === undefined) continue
    if (path.includes(`{${name}}`)) {
      path = path.replace(`{${name}}`, String(value))
      continue
    }
    for (const item of Array.isArray(value) ? value : [value]) {
      query.append(name, typeof item === 'object' ? JSON.stringify(item) : String(item))
    }
  }
  return { path, query }
}
