//! Hand-written core for the Bluesky client: the AppView's HTTP transport and the XRPC
//! error mapping.
//!
//! Every generated struct holds its core as an `Arc<dyn HttpEndpoint<DefaultMeta>>` from
//! `truewire_core` and calls `request` on it; this is the one place that knows how to
//! reach the AppView. Nothing here is generated, and regenerating never touches it.
//!
//! Every endpoint in this client is public: `public.api.bsky.app` answers them without
//! credentials. An access JWT (from `com.atproto.server.createSession`, which this client
//! does not wrap) is accepted for calls against `bsky.social`, where the same endpoints
//! also carry the viewer's own state.

use async_trait::async_trait;
use truewire_core::http::{RequestOptions, Response};
use truewire_core::serde_json::Value;
use truewire_core::{Error, HttpCall, HttpClient, HttpEndpoint, Result};

pub mod jetstream;

pub use jetstream::{Jetstream, JetstreamCore, JetstreamOptions, JETSTREAM};

use crate::meta::DefaultMeta;

/// The public AppView: every endpoint here, no credentials, rate-limited by IP.
pub const PUBLIC_APPVIEW: &str = "https://public.api.bsky.app";

/// Bluesky's own PDS entryway: the same endpoints with an access JWT.
pub const BSKY_SOCIAL: &str = "https://bsky.social";

/// What `Core::new` takes.
#[derive(Debug, Clone, Default)]
pub struct CoreOptions {
    /// The XRPC host. Defaults to the public AppView, or to `bsky.social` when
    /// `access_jwt` is given; a `truewire mock` address in tests.
    pub base_url: Option<String>,
    /// An access JWT, sent as a bearer token on every call. No endpoint here needs one.
    pub access_jwt: Option<String>,
    /// The HTTP client to send through; one is made when omitted.
    pub http: Option<HttpClient>,
}

/// The transport every XRPC group calls.
#[derive(Debug)]
pub struct Core {
    base_url: String,
    access_jwt: Option<String>,
    http: HttpClient,
}

impl Core {
    pub fn new(options: CoreOptions) -> Self {
        let fallback = if options.access_jwt.is_some() {
            BSKY_SOCIAL
        } else {
            PUBLIC_APPVIEW
        };
        let base_url = options.base_url.unwrap_or_else(|| fallback.to_string());
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            access_jwt: options.access_jwt,
            http: options.http.unwrap_or_default(),
        }
    }

    /// Headers for one call.
    ///
    /// The JWT travels whenever the client has one, public endpoint or not: on
    /// `bsky.social` it is what fills the viewer state on a profile or a post.
    fn headers(&self) -> Vec<(String, String)> {
        let mut headers = vec![("Accept".to_string(), "application/json".to_string())];
        if let Some(jwt) = &self.access_jwt {
            headers.push(("Authorization".to_string(), format!("Bearer {jwt}")));
        }
        headers
    }
}

#[async_trait]
impl HttpEndpoint<DefaultMeta> for Core {
    /// Send one request and return the decoded reply; a non-2xx status raises.
    ///
    /// Every endpoint here is a `GET` whose parameters travel in the query string.
    async fn request(&self, call: HttpCall<'_, DefaultMeta>) -> Result<Value> {
        // The write half is not implemented here, and this refuses rather than sending a
        // request that would fail confusingly on the wire.
        //
        // `meta.inject` names credentials the transport is supposed to put in the body at
        // send time -- an app password for `createSession`, a refresh token as the bearer
        // for `refreshSession`. The Python and TypeScript cores do that and hold the
        // resulting session; this one does not yet, because a session is mutable state
        // behind an `Arc<dyn HttpEndpoint>` whose `request` takes `&self`, so it needs an
        // async-aware lock and a real `tokio` dependency rather than a dev-dependency.
        //
        // Stated loudly and early for the same reason the generator prints a skipped
        // walker instead of emitting one without its guard: a client that is missing
        // something should say so where the caller meets it.
        if let Some(inject) = &call.meta.inject {
            return Err(Error::auth(format!(
                "the Rust core does not implement session handling yet, so it cannot serve \
                 an endpoint declaring `inject: {inject}`. The read half of this client \
                 needs no credentials and works; for the write half use the Python or \
                 TypeScript client. Tracked in NOTES.md."
            )));
        }
        if call.meta.public != Some(true) && self.access_jwt.is_none() {
            return Err(Error::auth(
                "this endpoint needs a session. The Rust core accepts an `access_jwt` you \
                 already hold, but cannot create one from an app password yet -- see \
                 NOTES.md."
                    .to_string(),
            ));
        }
        let mut path = call.path.to_string();
        let mut query: Vec<(String, Option<String>)> = Vec::new();
        if let Some(Value::Object(fields)) = call.request {
            for (name, value) in fields {
                let placeholder = format!("{{{name}}}");
                if path.contains(&placeholder) {
                    path = path.replace(&placeholder, &plain(&value));
                    continue;
                }
                // XRPC takes a list-valued parameter (`uris`, `actors`) as repeated keys,
                // which is what the AppView reads and what `truewire mock` matches. The
                // runtime's `query_from` would send the array as one JSON string.
                match value {
                    Value::Null => {}
                    Value::Array(items) => {
                        for item in items {
                            query.push((name.clone(), Some(plain(&item))));
                        }
                    }
                    other => query.push((name, Some(plain(&other)))),
                }
            }
        }
        let method = call.method.unwrap_or("GET").to_uppercase();
        let url = format!("{}/{}", self.base_url, path.trim_start_matches('/'));
        let mut options = RequestOptions::new().headers(self.headers()).query(query);
        if let Some(timeout) = call.options.timeout {
            options = options.timeout(timeout);
        }
        let response = self.http.request(&method, &url, options).await?;
        if response.status >= 400 {
            return Err(map_error(&method, &path, &response));
        }
        response.json()
    }
}

/// A dumped value as query or path text: a string as it is, anything else as JSON.
fn plain(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// Map a non-2xx XRPC answer onto the runtime's errors.
///
/// An XRPC error body is `{"error": "InvalidRequest", "message": "..."}`; both parts are
/// useful, so both go into the message. `400` is a bad request, `401`/`403` an auth
/// failure, `429` the rate limit; everything else is an API error.
fn map_error(method: &str, path: &str, response: &Response) -> Error {
    let text = response.text();
    let body = response
        .json()
        .unwrap_or_else(|_| Value::String(text.clone()));
    let detail = match (body.get("error").and_then(Value::as_str), body.get("message").and_then(Value::as_str)) {
        (Some(code), Some(message)) => format!("{code}: {message}"),
        (Some(code), None) => code.to_string(),
        _ => text.chars().take(200).collect(),
    };
    let message = format!("{method} {path}: HTTP {}: {detail}", response.status);
    let error = match response.status {
        400 => Error::bad_request(message),
        401 | 403 => Error::auth(message),
        429 => Error::rate_limited(message),
        _ => Error::api(message),
    };
    error.with_status(response.status).with_body(body)
}
