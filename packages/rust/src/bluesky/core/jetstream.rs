//! The Jetstream transport: one WebSocket per subscription, and nothing sent on it.
//!
//! Jetstream has no subscribe frame. A consumer opens
//! `wss://.../subscribe?wantedCollections=...&cursor=...` and the server pushes from the
//! moment the socket is accepted; closing the socket is what unsubscribes. So the
//! subscription's parameters become the connection URL's query string, the `channel` the
//! generated endpoint passes names the subscription only locally, and the dialect declines
//! both request verbs with `Outgoing::Nothing` -- which is exactly what the runtime's
//! `push` stream is for.
//!
//! Each subscription owns a connection because two subscriptions want two different query
//! strings, and a query string is fixed at connect time.

use async_trait::async_trait;
use truewire_core::serde_json::Value;
use truewire_core::ws::{Data, Dialect, Incoming, Outgoing, Socket, SocketOptions, SubscribeOptions};
use truewire_core::{Error, Result, Stream, StreamEndpoint, SubscribeCall};

/// One of the public Jetstream instances. The others (`jetstream1.us-east`,
/// `jetstream1.us-west`, `jetstream2.us-west`) serve the same stream.
pub const JETSTREAM: &str = "wss://jetstream2.us-east.bsky.network/subscribe";

/// The one channel a Jetstream connection carries; the name is local to this client.
const CHANNEL: &str = "subscribe";

/// Jetstream's wire dialect: every frame is a push, and nothing is ever sent.
#[derive(Debug, Clone, Copy, Default)]
pub struct Jetstream;

impl Dialect for Jetstream {
    type Request = ();
    type Reply = Value;
    type Notification = Value;
    type Params = ();

    fn parse(&self, frame: Data) -> Result<Incoming<Value, Value>> {
        Ok(Incoming::Push {
            channel: CHANNEL.to_string(),
            notification: frame.json()?,
        })
    }

    fn encode_request(&self, _id: u64, _request: &()) -> Result<Data> {
        Err(Error::logic("Jetstream sends nothing: there is no request to encode"))
    }

    fn subscribe(&self, _channel: &str, _params: Option<&()>) -> Result<Outgoing<()>> {
        Ok(Outgoing::Nothing)
    }

    fn unsubscribe(&self, _channel: &str, _params: Option<&()>) -> Result<Outgoing<()>> {
        Ok(Outgoing::Nothing)
    }
}

/// What `JetstreamCore::new` takes.
#[derive(Debug, Clone, Default)]
pub struct JetstreamOptions {
    /// The Jetstream instance to subscribe to; a `truewire mock` address in tests.
    pub url: Option<String>,
}

/// The transport the generated Jetstream endpoint calls.
#[derive(Debug)]
pub struct JetstreamCore {
    url: String,
}

impl JetstreamCore {
    pub fn new(options: JetstreamOptions) -> Self {
        Self {
            url: options.url.unwrap_or_else(|| JETSTREAM.to_string()),
        }
    }
}

#[async_trait]
impl StreamEndpoint for JetstreamCore {
    /// Open one connection with the subscription's parameters in its URL and hand back the
    /// frames it pushes.
    async fn subscribe(&self, call: SubscribeCall<'_, ()>) -> Result<Stream<Value>> {
        let url = format!("{}{}", self.url, query(call.parameters.as_ref()));
        let socket = Socket::new(Jetstream, SocketOptions::new(url));
        let stream = socket
            .subscribe(CHANNEL, None, SubscribeOptions::new())
            .await?;
        Ok(stream)
    }
}

/// The parameters as a query string, a list-valued one as repeated keys.
fn query(parameters: Option<&Value>) -> String {
    let Some(Value::Object(fields)) = parameters else {
        return String::new();
    };
    let mut pairs: Vec<String> = Vec::new();
    for (name, value) in fields {
        match value {
            Value::Null => {}
            Value::Array(items) => {
                for item in items {
                    pairs.push(format!("{}={}", encode(name), encode(&plain(item))));
                }
            }
            other => pairs.push(format!("{}={}", encode(name), encode(&plain(other)))),
        }
    }
    if pairs.is_empty() {
        String::new()
    } else {
        format!("?{}", pairs.join("&"))
    }
}

fn plain(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

/// `encodeURIComponent` for one query component.
fn encode(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char)
            }
            other => out.push_str(&format!("%{other:02X}")),
        }
    }
    out
}
