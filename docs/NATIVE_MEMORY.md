# Native memory

Vera's personal memory is an optional local feature in the Mac app. It starts off for existing and new installations. When it is off, Vera performs no memory retrieval, embedding, proposal generation, maintenance, or memory request assembly.

Approved memory stays in the same local SQLite database as native conversation history. The app does not synchronize it to Open WebUI, vera-api, an account, or another device. Model-generated changes are proposals. They do not create, update, merge, suppress, expire, prune, or delete approved memory until the user reviews and accepts them.

## Library

The Memory area organizes readable records into four sections:

* **You** contains profile details and response preferences.
* **Topics** contains recurring interests.
* **Areas** contains projects, goals, plans, and sustained domains.
* **People** contains durable relationship context.

Each row shows a name, concise summary, and last-updated date. The detail view shows readable durable details and provides edit, delete, and source controls. Embeddings, similarity scores, and retrieval metadata are not shown in the primary library.

Each record also has an internal category and bank. Banks scope recall without replacing the four user-facing sections. A record is either durable or episodic. Episodic records require an absolute expiry date and stop appearing in recall immediately after that date. An automatic groom then removes an approved episodic record once its expiry date is strictly before the current date in the user's calendar: a record expiring today survives until tomorrow, and durable records and records without an expiry are never touched.

## Chat behavior

Semantic recall uses the embeddings model configured in Settings, Memory, at the active OpenAI-compatible endpoint. The fixed first-slice limits are:

* 8 selected records per turn
* about 700 memory-context tokens per turn
* cosine similarity of at least 0.2
* semantic duplicate comparison at 0.9 similarity
* reviewable consolidation suggestions at 0.94 similarity within a bank
* deterministic ties by newest update and then stable record identifier
* selected bank scope, with `all` as the inclusive scope
* 1,000 approved records before a capacity-review proposal appears

The saved system prompt stays first. A clearly delimited block of approved, unexpired, unsuppressed memory follows it, then completed local conversation history. The block says that memory is untrusted background context, not instructions. Pending, deleted, dismissed, suppressed, expired, irrelevant, malformed, and failed-retrieval records are excluded.

If embeddings or extraction is unconfigured, unreachable, unauthorized, malformed, or slow, Vera sends the ordinary chat request without memory and shows an understandable local status. Native streaming, endpoint selection, conversation history, the saved system prompt, the tool loop, Apple Reminders, and tool preferences continue to work.

After an eligible completed turn, the optional extraction model receives only bounded user and assistant turn text plus a bounded summary of existing approved memory. It returns structured create, update, merge, suppress, expire, or delete proposals. Empty, private, excluded, failed, interrupted, or tool-only turns do not create proposals. A conversation can be excluded from memory suggestions from its sidebar menu.

The past-chat search control reads at most 48 recent messages from each of 12 local conversations, stops after 12 eligible completed turns or 48,000 text bytes, and creates reviewable proposals with direct source-conversation links. Text that matches credential, token, password, private-key, action-token, hidden-reasoning, function-code, or similar secret patterns is not sent to memory services and cannot be stored as a record or proposal.

## Legacy behavior and native differences

The repository contains `services/owui-functions/adaptive_memory_v3.valves.md`, which records the deployed Adaptive Memory v3 behavior and non-default tuning. Native memory retains these useful concepts:

| Adaptive Memory behavior | Native behavior |
|---|---|
| User-specific facts | Concise records with readable details and safe source references |
| Categories and banks | Typed categories and internal bank scope |
| Semantic recall | OpenAI-compatible embeddings with deterministic local ranking |
| Text and semantic duplicate handling | Exact proposals are skipped, similar content becomes an update proposal, and extracted creates reconcile to updates |
| Correction and deletion | User edits are immediate, model suggestions wait for review, and deletion immediately prevents later recall |
| Durable and episodic facts | Episodic records require an absolute expiry date and are excluded after it |
| Consolidation and bounded storage | Merge, consolidation, and capacity actions are reviewable proposals; past-expiry episodic records are groomed automatically |
| Clear status | Off, setup required, indexing, ready, pending review, unavailable, maintenance needed, and failed states |

The native design intentionally differs in these ways:

* Personal memory has no server runtime dependencies; it runs entirely against the local store.
* The only automatic removal is the expiry groom, and every removal writes an inspectable audit entry.
* Provider settings, credentials, authorization headers, action tokens, function code, and hidden reasoning are not stored as memory.
* Maintenance runs only while the app is active and memory is enabled.
* Optional service failure never blocks ordinary native chat.
* The novice surface does not expose ranking thresholds, embeddings, scores, clusters, or pruning controls.

## Review and maintenance

The review queue supports accept and dismiss for all proposal types. Accepted creates become approved records and are embedded before recall. Accepted updates and merges replace only the records named in the visible proposal. User edits invalidate the old embedding and reindex the approved text. Delete and suppress decisions remove the record from recall immediately.

Maintenance never performs network work when memory is off or the optional memory service is unavailable. Records expiring today and capacity pressure create proposals; they do not delete or prune records on their own.

The expiry groom is separate from proposal-driven maintenance. It runs at app launch and every 24 hours while the app stays running, only while memory is enabled, entirely against the local store. Each pass deletes approved episodic records whose expiry date is strictly before the current date, writes an audit entry for every removal through the memory change trail, and dismisses pending proposals whose target record no longer exists, so the review queue never dangles even when an earlier pass was interrupted. A pass with nothing to do is silent. A pass that removes or dismisses anything shows an outcome line on the Memory status card that clears itself after a short while; a failure stays visible until the next pass. Passes are idempotent, and a dry run reports the exact candidates without deleting anything.

At 1,000 approved records, the local repository rejects another approved create until the user accepts an actionable cleanup or consolidation proposal. The proposal names and links the exact records it would replace or remove.
