# Security and privacy model

## Intended protection

A member's private records are accessible through the product only to that member and their authorized personal processor. Other members and normal workspace-admin interfaces cannot browse those records. Ente stores ciphertext; a checkout of the code repository contains no memory data. Company sharing creates a separate encrypted summary object and does not publish original transcripts or private metadata.

Encryption uses libsodium secretstream XChaCha20-Poly1305 with authenticated final chunks. Per-member keys are wrapped using secretbox under a deployment root key. Identity is bound inside wrapped keys and encrypted records. Operational profiles, jobs, tokens' labels, settings and connector queues are encrypted too; bearer/refresh-token lookup uses hashes. IDs, timestamps, ownership/queue metadata and record sizes may remain observable to the storage operator.

## Trusted processor, not zero knowledge

The hosted app decrypts authorized content to process and query it. The deployment operator controls that runtime and its key material. A malicious or compromised operator can recover content by changing or inspecting the service. This design does **not** offer cryptographic protection against that operator.

Slack-only sign-in without a separate secret is enabled by this hosted key custody. LiteLLM Memory adopts Ente's storage and encryption formats, not Ente Photos' end-to-end client key model. The gateway and model provider also see the content selected for each authorized inference request. Browser and Slack output is plaintext for its intended viewer; recipients can retain what they have seen.

## Authorization

- Slack OIDC must provide a verified exact-domain email and the configured workspace. A suffix resemblance to the domain is insufficient.
- Browser/API eligibility is rechecked through Slack with a five-minute positive cache. A signed `user_change` event invalidates the cache immediately and revokes an ineligible member. Slack queries check membership freshly; failure to verify denies access. Reinstall the generated manifest when updating scopes/events.
- Profile events can omit email; they revoke immediately only when explicit fields prove ineligibility. Otherwise the next access performs a full check. A background task checks enrolled accounts every minute using the same five-minute cache, refreshing directory profiles and catching departures without a new visit. See Slack's [user_change contract](https://docs.slack.dev/reference/events/user_change/).
- Personal bearer credentials cannot change sharing, consent to another client, or administer the workspace.
- The bundled connector uses hosted device approval and terminal polling. Device and browser codes are separate high-entropy values; only the device code can redeem a credential. Consent is single-use and bound to the signed-in company member. Pending requests expire after ten minutes, polling is throttled, and failed local setup revokes its attempted credential. The browser reports success only after the connector confirms its local transaction committed. No local callback listener is used.
- Other remote MCP clients use OAuth authorization code with PKCE, exact registered callbacks, one-use codes and resource binding. Access tokens expire after one hour; rotating refresh tokens expire after 30 days. Reuse of a consumed refresh token revokes its connection family.
- The web and a verified personal Slack DM can use the requester's private records and eligible company publications. A shared Slack conversation cannot use private originals, even if the requester owns them.
- Slack answers require an eligible, enrolled requester and an owner-approved channel. A verified personal DM can use the requester's private evidence; every other conversation uses only shared publications. Other channel participants do not need to enroll: apps, guests and external participants can see a shared-memory answer in an approved channel. Already posted messages cannot be made unseen.
- Search selects authorized owner/audience partitions before decrypting. In-flight agent output is checked against current policy before delivery. A source can never grant permissions through its text.

## Sharing and removal

New work is private. Auto-sharing is an explicit member preference limited to future nonhistorical work. Older restrictive type policies remain enforced for compatibility. Private-topic scores at or above the deployment cutoff (`JEV_PRIVATE_THRESHOLD`, default `0.7`) keep an entry private, as do failed or incomplete checks. Filter explanations are owner-only and read existing cached classifications. Bulk sharing requires a browser-session preview and confirmation bound to the owner, current policy and reviewed records; workers recheck privacy before publication. Changing the cutoff rechecks cached scores without adding sharing consent. Adding a privacy rule withdraws existing publications immediately while cached classification results are reused.

Deletion first denies access and commits a tombstone, then removes source, derived, publication and affected overview objects with durable retries. Replay cannot silently resurrect the deleted source. Leaving revokes connections and processor/capture access and withdraws publications; leaving is not currently a hard purge of every retained record. Export first when leaving if a copy is wanted.

An eligible returning member can explicitly restore their own departed account after Slack sign-in. Their existing wrapped key/private history is retained, while old tokens stay revoked, capture stays paused and sharing remains off. This is not a way for an administrator to recover another person's account.

Installation transfers use a temporary recipient key and libsodium [sealed boxes](https://libsodium.gitbook.io/doc/public-key_cryptography/sealed_boxes). Sealed boxes protect recipient confidentiality and integrity; they do not authenticate the sender. Signed-in owner approval on both installations, exact source/destination binding and a one-transfer upload capability supply authorization. This browser flow requires the same Slack workspace/user identity on both sides.

The source stages only recipient-encrypted packets. The destination stages the same ciphertext and encrypts its temporary private key under the deployment's operational key. An encrypted final manifest binds packet order and record count; the complete transfer must verify before any archive import. The server never fetches a user-entered URL. Cross-origin requests omit cookies and use a scoped upload token; only the exact approved source origin receives CORS permission. The sender page's CSP permits that one validated destination, while the main app stays same-origin. The invitation travels in a URL fragment, immediately removed by the sender page; it never belongs in server query logs or referrers.

Receiving/upload grants expire after 30 minutes. Accepted imports get 24 hours for durable processing and a status receipt; uploads close immediately. Completion removes the receiving key and packet staging. A limited token hash remains until receipt expiry so the source tab can read counts/status, never memory content. Source staging is removed when that tab observes completion, or expires after the original 30 minutes. Cancellation removes the key and packets after any in-flight record completes. Already imported private records and source copies remain. Expiry is enforced on access even before periodic cleanup. Backups can retain encrypted staging and key envelopes until their own retention expires; protect deployment keys accordingly.

Optional offline encrypted exports use a separate user-chosen passphrase, Argon2id key derivation and authenticated secretstream framing, including a final record count. Wrong passwords, truncation, reordered frames and changed authenticated headers are rejected before restore starts. An export is an additional retained copy: deleting a live record does not remotely erase a downloaded file.

## Logging and browser handling

Every model request uses LiteLLM's no-log request option and message-redaction header. Agent SDK tracing is disabled. These are requests to the configured gateway, not proof that a provider or modified gateway cannot log content. Configure your own retention policies and test your gateway version, especially pass-through routes.

Optional Pydantic Logfire/OTLP instrumentation is manual and content-free. It does not auto-instrument HTTP bodies, model calls, Pydantic inputs or exception locals. Browser memory is kept in the current tab rather than local/session storage. Markdown is sanitized, remote content images are blocked in answers, and authenticated content responses use `Cache-Control: no-store`.

Keep TLS enabled, patch dependencies, protect persistent volumes and root keys, and do not commit diagnostics containing real histories. Contact the repository owner privately for a vulnerability; do not include secrets or private memories in a public issue.
