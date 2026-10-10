# Interface guide

LiteLLM Memory uses a chronological Activity feed, an explicit Ask interface, and private/company labels on each memory. The screenshots below show the actual application with fictional people and synthetic records. They do not demonstrate live Slack or model-provider behavior.

## Activity

Company shows shared work; My work shows the signed-in member's private and shared records. A person's page limits the feed to their shared activity. Questions use the selected audience, and source links remain subject to authorization.

![Activity with fictional people and synthetic work records](assets/dashboard.jpg)

## Ask

Choose the evidence scope and a gateway model, then ask a question. Responses link to source memories. Changing audience or person starts a separate conversation so private context is not carried into a wider audience.

![Ask with a fictional signed-in member](assets/ask.jpg)

## Sharing

New work starts private. Members can preview a single publication or review existing eligible memories before sharing them. Private-topic rules and workspace sharing controls remain in force.

![Sharing preferences for a fictional member](assets/settings.jpg)

## Memory detail

Memory detail shows the author, date, audience and recorded text. Original evidence and metadata remain in a separate disclosure; sharing does not publish private source metadata.

![A synthetic private memory](assets/memory.jpg)

## Review a change

Use the [repository UI skill](../.agents/skills/litellm-memory-ui/SKILL.md). Check the affected states at desktop and narrow widths, keyboard focus, navigation, loading and recovery. Inspect browser errors and verify the current build before replacing screenshots. Keep fixtures fictional and do not capture real memories, credentials or private infrastructure.
