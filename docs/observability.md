# Observe the agent with Pydantic Logfire

Logfire shows what the agent does, how long it takes, and where it fails. The SDK is already installed. litemem uses manual OpenTelemetry spans; it does not require switching to Pydantic AI or routing models outside LiteLLM.

## Connect Logfire

1. Create a Logfire project and a **write token** in its project settings. [Pydantic's instructions](https://pydantic.dev/docs/logfire/get-started/first-trace/).
2. Set two private environment variables on your deployment:

   ```dotenv
   OBSERVABILITY_EXPORT=logfire
   LOGFIRE_TOKEN=your-project-write-token
   ```

3. Restart the app. Ask a question, then open Logfire's Live view and filter by `service_name = 'litemem'`.

On Render, use the service's Environment settings and redeploy. With `scripts/self-host.sh`, add the values to the generated `.env.selfhost`, then run:

```sh
docker compose --env-file .env.selfhost up -d litemem
docker compose --env-file .env.selfhost exec litemem python -m moegent.observability
```

Look for `litemem.setup_check` to confirm the backend actually received the test trace. The command's successful completion alone does not prove delivery. Keep write tokens out of Git.

## What you can see

| Trace | Information |
| --- | --- |
| `litemem.query` | Web, Slack, MCP or API; selected model; private/company audience; duration; success, failure or cancellation; evidence count; model requests and token counts when the SDK reports usage. |
| `litemem.tool` | Search, read, people, overview or progress tool; duration and outcome. Tool spans belong to their query trace. |
| `litemem.classification` | Jev model, number of criteria, duration and outcome. Cached classifications generate no new model span. |
| `litemem.generation` / `embedding` | Configured model, duration and outcome; embedding batch size. |
| `litemem.request` | HTTP status and duration. No URL, request body or identity. |

This lets you compare channels and models, find slow searches, and spot repeated tool calls. It does not provide per-person tracking, transcripts, model reasoning, dollar-cost estimates, or an answer-quality score. Token counts depend on gateway/provider reporting. Review answer quality separately with the [evaluation workflow](answer-quality.md).

In Logfire's SQL Workbench, query usage by channel and outcome:

```sql
SELECT attributes->>'source' AS source,
       attributes->>'outcome' AS outcome,
       count(*) AS queries,
       avg(duration) * 1000 AS average_ms
FROM records
WHERE service_name = 'litemem'
  AND span_name = 'litemem.query'
  AND kind = 'span'
GROUP BY 1, 2
```

The time picker bounds the period. To investigate one request, open its trace and inspect the child tool spans. [Logfire SQL reference](https://pydantic.dev/docs/logfire/reference/sql/).

## Privacy and self-hosting

Tracing is **off by default**. The exporter accepts only listed operational attributes. It excludes prompts, answers, original records, tool arguments/results, member identities, credentials, exception messages and stack variables. Automatic HTTP/model instrumentation remains disabled. Tests check nested traces using private-content canaries.

For an existing OpenTelemetry-compatible backend instead of Logfire cloud:

```dotenv
OBSERVABILITY_EXPORT=otlp
OTLP_TRACES_ENDPOINT=http://your-collector:4318/v1/traces
```

Restart and run the same check. Use a collector reachable from the app container; `localhost` refers to that container. This mode does not send data to Logfire. Add collector authentication and TLS according to your infrastructure.

The Logfire SDK is open source; the hosted dashboard has its own plans and limits. Check [current Logfire plans](https://pydantic.dev/logfire) before enabling a production exporter. litemem does not require a paid telemetry service.
