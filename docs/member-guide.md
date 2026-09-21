# Using litemem

litemem keeps a private, searchable record of your work with AI. You decide when to ask it a question and what your company can see.

## Join and connect

Choose **Sign in with Slack** using your company account. Your installation accepts only its configured Slack workspace and verified email domain. There is no separate litemem password or key to save. If this is an owner-only pilot, other members cannot join yet.

In **Connections**, choose **Set up continuous capture** and follow the connector instructions on the computer running Codex or Claude Code. The connector adds hooks and a litemem MCP connection while keeping existing configuration. Codex requires reviewing and trusting its installed hooks. Restart your coding tool and authenticate its MCP connection when prompted. [Full connector instructions](connectors.md).

Recording captures visible user and assistant messages; it excludes system instructions, tool dumps and hidden reasoning. Remote MCP by itself cannot see your conversations. Nothing searches your history on every coding turn.

An encrypted local outbox retries uploads when you are offline. **Settings → Recording → Record new work** pauses hosted intake. To stop local buffering too, run `litemem-connector pause`. Resuming does not automatically sweep up the paused period; you can choose an explicit historical import later.

## Browse and ask

**Activity** shows your work newest first. Private items have a lock and a **Private** label. **Company** shows company publications, without a recommendation algorithm. Filters narrow by person, source, type and date; search also uses searchable metadata. Missing original dates stay unknown.

Use the question box on Activity or open **Ask**. Choose a model from your gateway's available models, ask about your own work or choose a person, and open citations to inspect the evidence. You can ask follow-up questions within the same scope. Changing person or audience asks before clearing an existing conversation. Browser Back returns to the previous view.

**People** uses enrolled members' Slack profiles. You can see their shared activity and ask about evidence they have made available. Another person's private counts, source transcripts and metadata are not exposed.

In **Activity → Filter → Exclude from my feed and Ask**, hide people's work, projects or sources from your feed, Ask and catch-ups. These personal filters are saved across sign-ins. They do not change anyone's access or your sharing choices, and originals remain available through their links and in exports. Activity shows **Personal filters are on · Edit** while exclusions apply. Clear the selections and save to include everything you can access again. Saving filters starts a fresh Ask conversation so earlier excluded material is not reused.

Project and source names match without regard to capitalization. Enter one per line. Existing type metadata remains searchable; older saved type exclusions are retained. Changing viewing choices does not call Jev again.

**Catch up** produces current, daily or project views from available evidence. These are derived views, with sources and coverage information; they do not rewrite the original messages. “Processing” means processing is still pending, not that the source was lost. Settings shows failed work and a retry control.

**Settings → General** lets you choose your display time zone or follow your device. Dates and feed date filters use that choice; original timestamps stay unchanged. A date without an original timezone is marked as incomplete and excluded from exact date ranges.

Workspace administrators also set the daily catch-up time and workspace timezone there. The default is 02:00. Daily catch-ups cover the previous workspace day and show which timezone defines it. On spring-forward days, a skipped time moves forward by the clock gap; on fall-back days, the first occurrence runs once. Missed runs combine into the latest due catch-up. Late work remains searchable and can refresh the affected view without rewriting originals.

In Slack, talk to **@litemem**. A verified personal DM can use your private history. Shared conversations use only company-public evidence that is safe for every participant. Slack membership failures or external/unknown participants cause the agent to withhold an answer. It cannot retract information someone already read.

## Control sharing

Everything starts private. **Company-public** means eligible members of your installation, never the open internet. Sharing may be disabled for the whole workspace during a pilot.

Open a memory to preview the separate summary that would be shared. The original evidence stays private. In **Settings → Privacy**, optionally enable **Share new summaries with my company**. Historical imports do not publish automatically.

Under **Never share these topics**, describe a topic to exclude. Jev checks existing and future source chunks, caching each result for the criterion and classifier version. Adding a rule checks that new rule; it does not rerun previously completed rules. Uncertain matches remain private, and the rule withdraws existing publications while checks run.

Use **Add correction** to add dated corrective evidence without overwriting the original. Deleting a memory removes access immediately and schedules storage cleanup; its deletion marker prevents an old import from bringing it back.

## Move or download your history

For another litemem installation, choose **Settings → Your data → Transfer history**, enter its HTTPS address, and sign in there with the same Slack account. Confirm receiving, return automatically, then review the destination and choose **Send my history**. There is no transfer password or key to copy.

Keep the tab open while it sends. If interrupted, return to that page and retry; packets already received are skipped. Once it says **Importing privately**, you can close the tab. **View recent imports** at the destination shows progress. If recording is paused, the import waits until you resume it. A transfer must arrive within 30 minutes; accepted imports have up to 24 hours to finish. Start again if it expires.

Your source copy stays in place. Imports preserve original text, dates, metadata and source identities, arrive privately, and are organized using the destination's models. Existing records are deduplicated; deletion markers and conflicts exclude records rather than overwrite them. The browser flow supports the same Slack identity on both installations, up to 100,000 records and 512 MB of staged encrypted data.

You can cancel from either progress page. Cancellation stops after any record already being saved; imported records remain private. It does not undo the import or delete the source.

For a file, choose **Encrypted export** or **Readable JSONL**. Only the optional encrypted file uses a password, which protects that download independently of Slack. Keep that password if you want to restore the file. JSONL is readable plaintext. [Offline restore instructions](self-hosting.md#personal-portability).

## Leave or return

**Settings → Your data → Leave** revokes your connections, capture and agent access and withdraws your publications. Export first if you want a copy. Leaving is not a permanent purge of every retained record.

If you are still eligible, signing in again offers an explicit restore of your departed account. Old credentials remain revoked and recording/sharing stay paused until you choose otherwise.

## What encryption protects

Persisted work is encrypted and normal administrators cannot browse your private records through litemem. The hosted application is a trusted processor: its runtime decrypts authorized content for search and inference. An operator controlling that runtime and its keys could access it. This is not protection against a malicious operator. Read the [privacy model](security.md) for that boundary and the gateway's role.
