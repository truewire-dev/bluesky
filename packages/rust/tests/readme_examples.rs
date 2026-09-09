//! The README's examples, compiled.
//!
//! `truewire docs check` type-checks Python examples with pyright and the TypeScript
//! package extracts and compiles its own; Rust has neither yet, so these are the same code
//! the page shows, wrapped in the async functions a snippet leaves implicit. Nothing runs:
//! `cargo test` compiling this file is the whole point.

#![allow(dead_code, unused_variables)]

use std::sync::Arc;

use bluesky::core::{Core, CoreOptions, JetstreamCore, JetstreamOptions};
use bluesky::Bluesky;
use futures::StreamExt;
use truewire_core::{CallOptions, Result};

async fn the_client() -> Result<()> {
    let client = Bluesky::new(
        Arc::new(Core::new(CoreOptions::default())),
        Arc::new(JetstreamCore::new(JetstreamOptions::default())),
    );

    let profile = client
        .actor
        .get_profile(
            bluesky::actor::get_profile::Request { actor: "bsky.app".into(), ..Default::default() },
            CallOptions::default(),
        )
        .await?;
    println!("{} {:?}", profile.handle, profile.followers_count);
    Ok(())
}

async fn calling_an_endpoint(client: &Bluesky) -> Result<()> {
    let feed = client
        .feed
        .get_author_feed(
            bluesky::feed::get_author_feed::Request {
                actor: "bsky.app".into(),
                limit: Some(3),
                filter: Some(bluesky::feed::get_author_feed::RequestFilter::PostsNoReplies),
                ..Default::default()
            },
            CallOptions::default(),
        )
        .await?;
    for entry in &feed.feed {
        println!("{} {}", entry.post.indexed_at, entry.post.record.text);
    }
    Ok(())
}

async fn subscribing(client: &Bluesky) -> Result<()> {
    let stream = client
        .jetstream
        .events(
            bluesky::jetstream::events::Parameters {
                wanted_collections: Some(vec!["app.bsky.feed.post".into()]),
                ..Default::default()
            },
            CallOptions::default(),
        )
        .await?;
    futures::pin_mut!(stream);
    while let Some(event) = stream.next().await {
        let event = event?;
        println!("{} {}", event.time_us, event.did);
    }
    Ok(())
}
