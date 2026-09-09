//! Every recorded example replayed through the generated Rust client against `truewire
//! mock`, which serves the same recordings over real HTTP and a real WebSocket. Nothing
//! here touches the network.
//!
//! The assertions are the Rust half of `packages/python/test/test_recordings.py` and
//! `packages/typescript/test/replay.test.ts`: structural, not literal, because the counts
//! and the text move with every re-recording and the shape does not. Where the other two
//! prove a thing about a response, this proves the same thing about the same response.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

use bluesky::core::{CoreOptions, JetstreamOptions};
use bluesky::types::{PostView, ProfileView, ProfileViewBasic};
use bluesky::Bluesky;
use futures::StreamExt;
use truewire_core::CallOptions;

/// The repository root, three levels above this package.
fn project_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("packages/rust sits two levels below the repository root")
        .to_path_buf()
}

/// `truewire mock`, on free ports, with the URLs it printed.
struct Mock {
    child: Child,
    http: String,
    ws: String,
}

impl Drop for Mock {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

fn start_mock() -> Mock {
    let root = project_root();
    let bin = std::env::var("TRUEWIRE_BIN")
        .map(PathBuf::from)
        .unwrap_or_else(|_| root.join(".venv/bin/truewire"));
    let mut child = Command::new(&bin)
        .args(["mock", "--project"])
        .arg(&root)
        .args(["--http-port", "0", "--ws-port", "0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap_or_else(|e| panic!("could not start {}: {e}", bin.display()));
    let stdout = child.stdout.take().expect("piped");
    let mut lines = BufReader::new(stdout).lines();
    let mut http = None;
    let mut ws = None;
    for line in lines.by_ref() {
        let line = line.expect("mock stdout");
        let mut parts = line.split_whitespace();
        match (parts.next(), parts.next()) {
            (Some("HTTP"), Some(url)) => http = Some(url.to_string()),
            (Some("WS"), Some(url)) => ws = Some(url.to_string()),
            _ => {}
        }
        if http.is_some() && ws.is_some() {
            break;
        }
    }
    // Keep draining after the URLs. The mock writes to stdout for the life of the process,
    // and a pipe nobody reads fills up: it then blocks on the write, or takes EPIPE if the
    // reader has gone, and dies mid-response. Two tests here never filled the buffer, so
    // this was latent; the weather.gov showcase runs eight and hit it immediately, as
    // `IncompleteBody` and `ConnectionReset` on three of them -- a bug in the harness
    // reading as flakiness in the client.
    std::thread::spawn(move || lines.for_each(drop));
    Mock {
        child,
        http: http.expect("the mock printed an HTTP url"),
        ws: ws.expect("the mock printed a WS url"),
    }
}

fn client(mock: &Mock) -> Bluesky {
    Bluesky::with_options(
        CoreOptions {
            base_url: Some(mock.http.clone()),
            ..Default::default()
        },
        JetstreamOptions {
            url: Some(mock.ws.clone()),
        },
    )
}

/// The post `feed.get_posts` and `feed.get_post_thread` both name, read from the example
/// rather than written here: a post can be deleted and `refresh_post_examples.py` repoints
/// both examples at a live one, which a constant would turn into a failure.
fn pinned_post() -> String {
    let path = project_root().join("spec/endpoints/feed/get_posts/examples/bsky_app_hello.request.json");
    let text = std::fs::read_to_string(path).expect("the get_posts example");
    let value: truewire_core::serde_json::Value =
        truewire_core::serde_json::from_str(&text).expect("valid json");
    value["request"]["uris"][0]
        .as_str()
        .expect("one pinned uri")
        .to_string()
}

fn is_profile_basic(profile: &ProfileViewBasic) {
    assert!(profile.did.starts_with("did:"), "{}", profile.did);
    assert!(!profile.handle.is_empty());
}

fn is_profile(profile: &ProfileView) {
    assert!(profile.did.starts_with("did:"), "{}", profile.did);
    assert!(!profile.handle.is_empty());
}

fn is_post(post: &PostView) {
    assert!(post.uri.starts_with("at://"), "{}", post.uri);
    assert!(!post.cid.is_empty());
    is_profile_basic(&post.author);
}

#[tokio::test]
async fn every_recorded_example_replays_through_the_client() {
    let mock = start_mock();
    let client = client(&mock);
    let options = CallOptions::default();
    let pinned = pinned_post();

    let profile = client
        .actor
        .get_profile(
            bluesky::actor::get_profile::Request { actor: "bsky.app".into(), ..Default::default() },
            options.clone(),
        )
        .await
        .expect("actor.get_profile");
    assert_eq!(profile.handle, "bsky.app");
    assert!(profile.followers_count.unwrap_or(0) > 0);

    let profiles = client
        .actor
        .get_profiles(
            bluesky::actor::get_profiles::Request {
                actors: vec!["bsky.app".into(), "atproto.com".into()],
                ..Default::default()
            },
            options.clone(),
        )
        .await
        .expect("actor.get_profiles");
    let handles: Vec<&str> = profiles.profiles.iter().map(|p| p.handle.as_str()).collect();
    assert_eq!(handles, vec!["bsky.app", "atproto.com"]);
    for view in &profiles.profiles {
        // `get_profiles` answers with `ProfileViewDetailed`, a wider record than the
        // `ProfileView` the graph endpoints return, so this asserts the shared fields
        // rather than reusing `is_profile`.
        assert!(view.did.starts_with("did:"), "{}", view.did);
        assert!(!view.handle.is_empty());
    }

    let posts = client
        .feed
        .get_posts(
            bluesky::feed::get_posts::Request { uris: vec![pinned.clone()], ..Default::default() },
            options.clone(),
        )
        .await
        .expect("feed.get_posts");
    assert_eq!(posts.posts.len(), 1, "a post that is gone comes back as an empty array");
    assert_eq!(posts.posts[0].uri, pinned);
    is_post(&posts.posts[0]);

    let resolved = client
        .identity
        .resolve_handle(
            bluesky::identity::resolve_handle::Request { handle: "bsky.app".into(), ..Default::default() },
            options.clone(),
        )
        .await
        .expect("identity.resolve_handle");
    assert!(resolved.did.starts_with("did:"));

    let followers = client
        .graph
        .get_followers(
            bluesky::graph::get_followers::Request {
                actor: "atproto.com".into(),
                limit: Some(3),
                ..Default::default()
            },
            options.clone(),
        )
        .await
        .expect("graph.get_followers");
    assert_eq!(followers.subject.handle, "atproto.com");
    is_profile(&followers.subject);
    assert!(!followers.followers.is_empty());
    for follower in &followers.followers {
        is_profile(follower);
    }
}

#[tokio::test]
async fn the_recorded_jetstream_capture_replays_over_a_real_websocket() {
    let mock = start_mock();
    let client = client(&mock);
    let stream = client
        .jetstream
        .events(
            bluesky::jetstream::events::Parameters {
                wanted_collections: Some(vec!["app.bsky.feed.post".into()]),
                ..Default::default()
            },
            CallOptions::default(),
        )
        .await
        .expect("jetstream.events");
    futures::pin_mut!(stream);
    let mut seen = 0;
    while let Some(event) = stream.next().await {
        let event = event.expect("a decoded event");
        assert!(event.did.starts_with("did:"), "{}", event.did);
        seen += 1;
        if seen >= 3 {
            break;
        }
    }
    assert_eq!(seen, 3);
}
