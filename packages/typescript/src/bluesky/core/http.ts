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

/** A live session: the two tokens, and who they belong to. Held in memory, never stored. */
export interface Session {
  accessJwt: string
  refreshJwt: string
  did: string
  handle: string
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
  /** The account's handle or DID, such as `truewire.dev`. Paired with `appPassword`. */
  identifier?: string
  /**
   * An app password from bsky.app > Settings > App Passwords. Never the account password:
   * an app password is individually revocable and cannot change the account's password or
   * email. Held in memory, sent only to `com.atproto.server.createSession`.
   */
  appPassword?: string
  /** Validate responses by default; a call's own `validate` option overrides it. */
  validate?: boolean
  /** The `fetch` wrapper to send through; one is made when omitted. */
  http?: HttpClient
}

/** The transport every XRPC group calls: `HttpEndpoint<DefaultMeta>` by shape. */
export class Transport implements HttpEndpoint<DefaultMeta> {
  readonly baseUrl: string
  readonly accessJwt: string | undefined
  readonly identifier: string | undefined
  readonly appPassword: string | undefined
  private session: Session | undefined
  readonly validate: boolean
  readonly http: HttpClient

  constructor(options: TransportOptions = {}) {
    const credentialed =
      options.accessJwt !== undefined ||
      (options.identifier !== undefined && options.appPassword !== undefined)
    const fallback = credentialed ? BSKY_SOCIAL : PUBLIC_APPVIEW
    this.baseUrl = (options.baseUrl ?? fallback).replace(/\/+$/, '')
    this.accessJwt = options.accessJwt
    this.identifier = options.identifier
    this.appPassword = options.appPassword
    this.validate = options.validate ?? true
    this.http = options.http ?? new HttpClient()
  }

  /** Send one call; the reply's body, validated unless `validate` is off. */
  async request<Req, Res>(call: HttpCall<Req, Res, DefaultMeta>): Promise<Res> {
    const inject = call.meta.inject
    const isPublic = call.meta.public === true
    // A session is created before *any* call this transport can authenticate, not only a
    // non-public one: on `bsky.social` a read needs one where the same read needs nothing
    // on the public AppView, so a credentialed client would 401 on every read otherwise.
    // `password` is the exception -- it is how a session is made, and ensuring one would
    // recurse.
    if (inject !== 'password') await this.ensureSession()

    const { fields, bearer } = this.injected(inject)
    const values =
      call.request !== undefined && call.requestCodec !== undefined
        ? (call.requestCodec.dump(call.request) as Record<string, unknown>)
        : {}
    const method = call.method ?? 'GET'
    const { path, query } = fill(call.path, values)
    // The same values the query was built from, minus anything the path consumed and
    // anything unset -- the shape a JSON body wants.
    const body_values = Object.fromEntries(
      Object.entries(values).filter(
        ([name, value]) =>
          value !== null && value !== undefined && !call.path.includes(`{${name}}`),
      ),
    )

    const send = async (): Promise<string> => {
      const headers = this.headers(isPublic, bearer)
      let body: string | undefined
      if (method === 'POST' || method === 'PUT' || method === 'PATCH') {
        // Built from the rendered values, not from `query`. A query string flattens every
        // value to a string, which is right for a GET and wrong for a body: `record` is a
        // nested object, and stringifying it makes the server (and the mock) reject the
        // call as an unexpected parameter.
        const merged = { ...body_values, ...fields }
        if (Object.keys(merged).length > 0) {
          body = JSON.stringify(merged)
          // XRPC refuses a body with no declared encoding.
          headers['Content-Type'] = 'application/json'
        }
      }
      const response = await this.http.request(method, this.baseUrl + path, {
        query: body === undefined ? query : undefined,
        headers,
        body,
        signal: call.signal,
      })
      const text = await response.text()
      if (response.status >= 400) raiseForStatus(method, path, response.status, text)
      return text
    }

    let text: string
    try {
      text = await send()
    } catch (error) {
      // Only a session this transport owns can be recovered; a caller-supplied `accessJwt`
      // is theirs, and re-sending would fail identically.
      if (!(error instanceof AuthError) || inject !== undefined || this.session === undefined) {
        throw error
      }
      await this.refresh()
      text = await send()
    }

    if (call.responseCodec === undefined) return undefined as Res
    const value: unknown = text === '' ? undefined : JSON.parse(text)
    return (call.validate ?? this.validate) ? call.responseCodec.parse(value) : (value as Res)
  }

  /**
   * What the transport adds to one call that the caller never passed.
   *
   * `password` fills `createSession`'s two credential fields; `refresh` swaps the access
   * token for the refresh token. This is what keeps both out of every request schema, and
   * therefore out of every recorded example.
   */
  injected(inject: string | undefined): { fields: Record<string, unknown>; bearer?: string } {
    if (inject === 'password') {
      if (this.identifier === undefined || this.appPassword === undefined) {
        throw new AuthError('creating a session needs identifier and appPassword on Bluesky.new()')
      }
      return { fields: { identifier: this.identifier, password: this.appPassword } }
    }
    if (inject === 'refresh') {
      if (this.session === undefined) throw new AuthError('there is no session to refresh')
      return { fields: {}, bearer: this.session.refreshJwt }
    }
    return { fields: {} }
  }

  /** Exchange the app password for a session, and remember it. */
  async signIn(): Promise<Session> {
    const { fields } = this.injected('password')
    return this.remember(await this.post('/xrpc/com.atproto.server.createSession', fields))
  }

  /** Trade the refresh token for a new session; the refresh token is the bearer. */
  async refresh(): Promise<Session> {
    const { bearer } = this.injected('refresh')
    return this.remember(
      await this.post('/xrpc/com.atproto.server.refreshSession', undefined, bearer),
    )
  }

  private async post(
    path: string,
    fields?: Record<string, unknown>,
    bearer?: string,
  ): Promise<Record<string, string>> {
    const headers = this.headers(true, bearer)
    let body: string | undefined
    if (fields !== undefined && Object.keys(fields).length > 0) {
      body = JSON.stringify(fields)
      headers['Content-Type'] = 'application/json'
    }
    const response = await this.http.request('POST', this.baseUrl + path, { headers, body })
    const text = await response.text()
    if (response.status >= 400) raiseForStatus('POST', path, response.status, text)
    return JSON.parse(text) as Record<string, string>
  }

  private remember(payload: Record<string, string>): Session {
    this.session = {
      accessJwt: payload.accessJwt!,
      refreshJwt: payload.refreshJwt!,
      did: payload.did!,
      handle: payload.handle!,
    }
    return this.session
  }

  /** Create a session before an authenticated call, where this transport can. */
  async ensureSession(): Promise<void> {
    if (this.session !== undefined || this.accessJwt !== undefined) return
    if (this.identifier === undefined || this.appPassword === undefined) return
    await this.signIn()
  }

  /** Whether this transport can produce a bearer token for a non-public call. */
  get canAuthenticate(): boolean {
    return (
      this.accessJwt !== undefined ||
      this.session !== undefined ||
      (this.identifier !== undefined && this.appPassword !== undefined)
    )
  }

  /**
   * Headers for one call.
   *
   * The JWT travels whenever the client has one, public endpoint or not: on
   * `bsky.social` it is what fills the viewer state on a profile or a post. A non-public
   * call without one is refused here, before any request is made.
   */
  private headers(isPublic: boolean, bearer?: string): Record<string, string> {
    const token = bearer ?? this.session?.accessJwt ?? this.accessJwt
    if (token !== undefined) {
      return { Accept: 'application/json', Authorization: `Bearer ${token}` }
    }
    if (!isPublic) {
      throw new AuthError(
        'this endpoint needs a session; pass identifier and appPassword (or an accessJwt) ' +
          'to Bluesky.new()',
      )
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
