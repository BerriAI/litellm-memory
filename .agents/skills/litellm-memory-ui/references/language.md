# Product language

## Voice

Use plain, calm, specific English. The product is capable without sounding theatrical. Labels name tasks and objects; descriptions explain consequences; errors explain recovery. Avoid marketing claims, cheering, synthetic personality, and security theater.

Use sentence case, ordinary verbs, and short paragraphs. “Connect a tool”, “Save time zone”, and “Ask about Tin’s shared work” communicate an outcome. “Unlock your collective intelligence”, “Memory orchestration”, and “Activate seamless insights” do not.

Do not add a subtitle just to fill the space beneath a heading. Do not put a sentence of implementation detail on every card. If text repeats the control label without resolving uncertainty, remove it.

## Vocabulary

| Say | Meaning / avoid |
| --- | --- |
| LiteLLM Memory | Product name; repository/package is `litellm-memory`; Slack app is **LiteLLM Memory**, agent mention is `@memory` |
| LiteLLM Labs | Product affiliation; avoid inventing brand variants |
| Activity | Chronological work, not recommendations |
| My work | The person's own work context |
| Company | Work shared within the permitted company audience; not the open Internet |
| Memory / record | Stored evidence of work; not a mandatory “learning” |
| Summary | Derived account of work; distinct from original evidence |
| Original conversation | Preserved source; not automatically exposed by sharing a summary |
| Private | Restricted to the relevant owner at the product access layer |
| Private topics | User-written exclusion instructions checked through Jev |
| Ask | Explicit querying; avoid “chatbot console” |
| Connections | Tools authorized to record/query; not a list of storage providers |
| Record / capture | Saving work; different from searching it |
| Jev / TypeSafe Jev | Correct product name; not Gev, JAV, or Java |
| LiteLLM gateway | The configured inference connection and model catalog |
| Logfire | Pydantic's observability product; not a second model provider |

Use legacy names only when a migration explanation requires them. Internal package `moegent` is not the user-facing product. Do not revive “product team knowledge” as the memory source for the new app without verifying that a specific legacy import is being described.

## Privacy copy is functional

Privacy information changes a person's decision, so keep it clear without turning every page into a warning. Pair the private lock with text. “Company” means the implemented audience; avoid an unexplained globe icon suggesting Internet publication.

| Context | Useful copy | Misleading copy |
| --- | --- | --- |
| Private entry | Private | Secure, with no audience specified |
| Home Ask | Your questions and answers stay private. | Ask the company, when that sounds like broadcasting |
| Future sharing | Original conversations and existing history stay private. | Everything is shared automatically. |
| Private topic | Keep this topic out of shared summaries. | Delete this from memory. |
| Add topic queued | Topic added. Existing and future work will be checked. | Your entire history is now filtered. |
| Personal viewing exclusions | These filters do not change what you share. | Privacy filters, when they only hide results for the viewer |
| Encrypted storage | Stored encrypted with Ente. | Even the server operator can never read it. |
| Web query scope | Use accessible memories. | Make this conversation public. |

The hosted processor holds decryption keys in the current architecture. Do not describe it as zero knowledge against its operator. The admin UI lacks general access to private memories, but that is different from cryptographic inability. Detailed key-custody explanations belong in security documentation or an appropriate setup context, not every feed row.

Slack channel answers are constrained by the relevant audience checks. Do not promise that a private DM's access applies to a shared channel. UI copy cannot substitute for server authorization.

## Labels, hints, examples

A label names the field: “Gateway URL”, “Your time zone”, “Never share work about”. The placeholder demonstrates: `https://gateway.example.com`, a short topic, or a concise question. Help states a non-obvious consequence: “Jev checks each topic against existing and future work.”

Do not use a large example as the label. Do not put critical instructions only in the placeholder. Avoid examples longer than the typical value the field accepts. Never put real credentials, customer content, or a person's private data in placeholder/demo text.

Required/optional meaning follows the real form contract. Do not label an input “optional” simply because the HTML lacks `required` if the server needs it. Do not show “Saved” merely because a local value changed.

## Action words

Use verb + object when ambiguity exists: Save models, Add topic, Transfer history, Export memories. Short labels like Add or Save are acceptable only when the immediately adjacent context makes the target obvious. Avoid generic Continue for an irreversible or surprising action.

Use specific destructive labels: Leave workspace, Remove topic, Revoke connection. State the scope in confirmation text. A user should not have to infer whether “Remove” means hide a view, delete a record, or revoke access.

## State language

- Loading: what is being loaded, without a fabricated time estimate.
- Checking: a dependency or configuration is being validated.
- Queued: accepted for future work; not completed.
- Processing: work is underway according to server status.
- Saved: persistence succeeded; may coexist with subsequent processing.
- Up to date: no outstanding processing reported; not proof of answer quality.
- Failed: the attempted operation did not finish; say what can be done next.
- Cancelled: the user stopped it; don't present as a system error.
- Disconnected: no active connection; not necessarily a deleted history.

Never equate “tests passed” with “live integrations working”. The local review uses synthetic records and simulated AI. Screenshots and walkthrough captions must preserve that distinction.

## Errors

Prefer a short statement of the problem plus recovery. Examples:

- “Couldn’t load the team directory. Try again.”
- “Couldn’t save this topic. Your text is still here.”
- “Jev isn’t available through this gateway. Enable it, then check again.”
- “Your session expired. Sign in with Slack to continue.”

Use these only when true. Do not promise retained input after code has already cleared it. Surface validation specifics where safe. Avoid stack traces, raw exception payloads, key fragments, accusatory language, and “Oops!” as the only explanation.

## Writing review

Read the page in DOM order. The sequence should make sense without its colors or whitespace. Each heading identifies a group, each label identifies a field, and each button identifies an action. Prefer deleting redundant copy to shrinking the font. Do not let a concise sentence become inaccurate about scope, privacy, or timing.
