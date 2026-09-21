# Using LiteLLM Memory

LiteLLM Memory keeps a private, searchable record of your work with AI. You decide when to ask it a question and what your company can see.

## Join and connect

Choose **Sign in with Slack** using your company account. Your installation accepts only its configured Slack workspace and verified email domain. There is no separate LiteLLM Memory password or key to save. If this is an owner-only pilot, other members cannot join yet.

After joining, Company opens immediately so you can browse shared work. Choose **Connect a tool** on Activity, or open **Connections**. Select Codex or Claude Code, copy the installation commands and run them on your computer. Restart the tool and finish its MCP sign-in; Codex also requires reviewing and trusting the installed hooks. The connector preserves existing configuration. [Full connector instructions](connectors.md).

Send a message and let your tool finish its reply. Connections first shows **Connected. Waiting for work.**, then confirms when a record arrives and offers **View my work**. It checks while that page is visible; **Check connection** refreshes immediately. Authorization alone, imported history and an upload still awaiting storage are not proof of new work arriving. A receipt confirms saved work, not that your tool is currently running. **Import earlier work** is optional.

Recording captures visible user and assistant messages; it excludes system instructions, tool dumps and hidden reasoning. Remote MCP by itself cannot see your conversations. Nothing searches your history on every coding turn.

An encrypted local outbox retries uploads when you are offline. **Settings → Recording → Record new work** pauses hosted intake. To stop local buffering too, run `litellm-memory-connector pause`. Resuming does not automatically sweep up the paused period; you can choose an explicit historical import later.

## Browse and ask

**Activity** opens on **Company**, showing company publications newest first, without a recommendation algorithm. **My work** is the second tab and shows your own private and shared records. Private items have a lock and a **Private** label. The Activity question box asks about the selected feed. Filters narrow by person, source, type and date; search also uses searchable metadata. Missing original dates stay unknown.

Use the question box on Activity or open **Ask**. Choose a model from your gateway's available models, ask about your own work or choose a person, and open citations to inspect the evidence. You can ask follow-up questions within the same scope. Changing person or audience asks before clearing an existing conversation. Browser Back returns to the previous view. Person feeds and My work have URLs that preserve their scope on reload; the home URL opens Company. Use **Refresh** to fetch the latest feed without reloading the app.

**People** uses enrolled members' Slack profiles. You can see their shared activity and ask about evidence they have made available. Another person's private counts, source transcripts and metadata are not exposed.

In **Settings → General → Browsing → Exclude from my feed and Ask**, hide people's work, projects or sources from your feed and answers. These personal filters are saved across sign-ins. They do not change anyone's access or your sharing choices, and originals remain available through their links and in exports. Activity shows **Personal filters are on · Edit** while exclusions apply. Clear the selections and save to include everything you can access again. Saving filters starts a fresh Ask conversation so earlier excluded material is not reused.

Project and source names match without regard to capitalization. Enter one per line. Existing type metadata remains searchable; older saved type exclusions are retained. Changing viewing choices does not call Jev again.

Ask questions such as “What did Tin work on yesterday?” directly in Ask. There is no separate Catch up screen or automatic daily-summary sweep. Requested overviews use dated sources and do not rewrite originals. **Settings → Recording** shows processing failures and a retry action.

**Settings → General** lets you choose your display time zone or follow your device. Dates and feed date filters use that choice; original timestamps stay unchanged. A date without an original timezone is marked as incomplete and excluded from exact date ranges.

In Slack, talk to **@memory**. A verified personal DM can use your private history. Shared conversations use only company-public evidence that is safe for every participant. Slack membership failures or external/unknown participants cause the agent to withhold an answer. It cannot retract information someone already read.

## Control sharing

Everything starts private. **Company-public** means eligible members of your installation, never the open internet. Sharing may be disabled for the whole workspace during a pilot.

Open a memory to preview the separate summary that would be shared. The original evidence stays private. In **Settings → Privacy**, optionally enable **Automatically share new summaries**. Historical imports do not publish automatically.

Under **Never share work about**, describe a topic to exclude. Jev checks existing and future source chunks, caching each result for the criterion and classifier version. Adding a rule checks that new rule; it does not rerun previously completed rules. Uncertain matches remain private, and the rule withdraws existing publications while checks run.

Open an entry’s **More actions → Add correction** to add dated corrective evidence without overwriting the original. Deleting a memory removes access immediately and schedules storage cleanup; its deletion marker prevents an old import from bringing it back.

## Move or download your history

For another LiteLLM Memory installation, choose **Settings → Your data → Transfer history**, enter its HTTPS address, and sign in there with the same Slack account. Confirm receiving, return automatically, then review the destination and choose **Send my history**. There is no transfer password or key to copy.

Keep the tab open while it sends. If interrupted, return to that page and retry; packets already received are skipped. Once it says **Importing privately**, you can close the tab. **View recent imports** at the destination shows progress. If recording is paused, the import waits until you resume it. A transfer must arrive within 30 minutes; accepted imports have up to 24 hours to finish. Start again if it expires.

Your source copy stays in place. Imports preserve original text, dates, metadata and source identities, arrive privately, and are organized using the destination's models. Existing records are deduplicated; deletion markers and conflicts exclude records rather than overwrite them. The browser flow supports the same Slack identity on both installations, up to 100,000 records and 512 MB of staged encrypted data.

You can cancel from either progress page. Cancellation stops after any record already being saved; imported records remain private. It does not undo the import or delete the source.

For a file, choose **Encrypted export** or **Readable JSONL**. Only the optional encrypted file uses a password, which protects that download independently of Slack. Keep that password if you want to restore the file. JSONL is readable plaintext. [Offline restore instructions](self-hosting.md#personal-portability).

## Leave or return

**Settings → Your data → Leave** revokes your connections, capture and agent access and withdraws your publications. Export first if you want a copy. Leaving is not a permanent purge of every retained record.

If you are still eligible, signing in again offers an explicit restore of your departed account. Old credentials remain revoked and recording/sharing stay paused until you choose otherwise.

## What encryption protects

Persisted work is encrypted and normal administrators cannot browse your private records through litellm-memory. The hosted application is a trusted processor: its runtime decrypts authorized content for search and inference. An operator controlling that runtime and its keys could access it. This is not protection against a malicious operator. Read the [privacy model](security.md) for that boundary and the gateway's role.
