# Evaluating work-history answers

`litellm-memory-evaluate` runs explicit questions through the deployed `/api/v1/chat` and owned job APIs. It uses the existing connector's Slack sign-in, takes chat models from the LiteLLM-backed catalog, and saves an encrypted local journal. It does not import work, change sharing, send Slack messages or ask a second model to grade its own answer.

This checks **API answer quality**. It does not replace live Slack audience tests, original-record reconciliation, the separate review of derived work entries, or the other [release gates](release-checklist.md). No real-history score has been measured yet. The automated test suite uses synthetic fixtures only.

## Prepare a private question set

Connect your own account with `litellm-memory-connector` first. Recording can remain paused: evaluation refreshes OAuth without uploading queued captures. The target installation needs functioning LiteLLM/Jev processing and the approved historical import before a real-history run is useful.

Keep the JSON question file **outside Git checkouts**, with permissions `0600`. It contains private questions, expected evidence and possibly private canaries. Use opaque UUIDs for dataset/case IDs; do not encode personal facts in IDs or model names. A minimal synthetic example:

```json
{
  "version": 1,
  "id": "aa7dfbd2-ed61-4a39-9df7-5aeb8b92569c",
  "corpus": "synthetic",
  "owner": "T_EXAMPLE:U_EXAMPLE",
  "deployment_revision": "0000000000000000000000000000000000000000",
  "cases": [
    {
      "id": "b7f6eeb6-2a3a-4393-94e1-e0505380849d",
      "category": "changed_decision",
      "question": "Did we ship the proposed export format?",
      "rubric": "The first record proposes a format. The later correction postpones it. Explain the changed decision and do not claim that it shipped.",
      "expectation": "answer",
      "audience": "owner",
      "required_citations": ["original_record_id", "correction_record_id"],
      "forbidden_citations": [],
      "forbidden_text": []
    }
  ]
}
```

Use the connected `team_id:user_id` for `owner`, the actual source record IDs, and the full tested deployment commit for `deployment_revision`. The revision and `real_history` label are **operator declarations**, not proof that the server or corpus matches them. Record import reconciliation and deployed revision separately in the release evidence. The runner refuses a different connected identity or installation when resuming.

Build the questions from independently inspected originals before looking at generated answers. Include at least 100 distinct, representative questions and at least five from each category:

| Category | What to test |
| --- | --- |
| `exact_id` | Locate a real ticket, PR, document or other artifact. |
| `paraphrase` | Ask in different words from those in the source. |
| `changed_decision` | Prioritize a dated correction while retaining history. |
| `unknown_date` | Avoid inventing dates or treating updates as creation. |
| `incomplete_history` | Explain the limits of partial capture/import coverage. |
| `conflicting_perspectives` | Preserve different people's statements and attribution. |
| `revoked` | Withhold formerly available evidence after actual access revocation. |
| `private` | Keep private facts and source IDs out of shared-audience answers. |
| `unsupported` | Qualify or abstain when the archive does not support an answer. |

Include at least ten `answer` and ten `abstain` expectations. `answer` cases require expected citations. Every privacy/revocation case needs forbidden source IDs or nonempty text canaries; include a `private` case with `audience: public`. Public means company-shared memory, and does not exercise real Slack channel membership. Setting `scope` to a member ID limits the question to that person's accessible work.

Choose required citations carefully: they are a strict minimum, so list records genuinely needed to support the answer. Legitimate alternative evidence can otherwise produce a conservative failure. Golden evidence must not be changed merely to match a generated answer; revise the question set and start a new run if the original rubric was wrong. Do not put private canary values in a public question itself. Source IDs and forbidden strings are checked literally (text is case-insensitive); paraphrased disclosures still require human review.

## Run and resume

```sh
chmod 600 /private/path/questions.json
litellm-memory-evaluate start /private/path/questions.json
# Optional: --model <an actual chat model ID from this installation's LiteLLM catalog>
litellm-memory-evaluate resume <run-uuid>
litellm-memory-evaluate cancel <run-uuid>
```

Use `--directory /private/path/connector-state` before the command when using a nondefault connector directory. Runs live in that directory's `evaluations/` folder. Inputs, answers and judgments are encrypted with its local vault key and written atomically with owner-only file permissions. The local account holding that key can decrypt them; this is the same local trust boundary as the connector, not protection from the device owner. Keep the directory out of backups or sharing locations that are inappropriate for your work history.

One case runs at a time with five-second polling, preserving capacity for interactive use. Each query has a persisted idempotency key. A lost response or interrupted process resumes the same job; it does not silently spend money on a fresh answer. A 429, authentication failure, access change or network failure stops the command with a generic message and retains the journal. Resolve the cause and resume. After ten minutes waiting on a case, the command stops locally while the owned server job remains available; resume or cancel explicitly. Failed server jobs count against the score rather than disappearing from its denominator. A new experiment needs a new run.

Cancellation stops known pending jobs and prevents unstarted cases from running. An uncertain submission is reconciled with its existing request ID before cancellation; a job may finish before cancellation reaches it. An interrupted cancellation remains a cancellation when resumed. Nothing changes record visibility or membership.

## Review and report

```sh
litellm-memory-evaluate review <run-uuid>
litellm-memory-evaluate report <run-uuid>
```

`review` deliberately displays private question text, evidence rubrics and answers in your terminal. Use a private terminal with appropriate scrollback/logging settings. Read the cited original records in your authenticated dashboard, then judge correctness/support or appropriate qualification/abstention, and whether every disclosed fact is allowed for the case's audience. Plans presented as completed work, invented attribution and missing corrections fail the supported-answer judgment. `s` skips without passing; `q` preserves progress. Judgments are bound to the exact answer hash and saved after each case. Terminal control characters in source/model text are escaped.

Other commands print only run IDs and aggregate metadata, never questions, answers, private canaries, tokens or upstream error bodies. Reports contain model IDs and a declared deployment commit; review this operational metadata before sharing it. The encrypted journal retains the full experiment privately. A local report remains a snapshot of historical evaluation evidence, even if remote access later changes; it does not grant ongoing server access or establish current policy correctness.

The report includes exact citation/canary checks and reviewed scores. It refuses a passing question-quality gate unless all cases finish and receive review, the real-history declaration and coverage requirements are met, supported answers reach **90%**, appropriate qualification/abstention reaches **95%**, and there are **zero detected or reviewer-observed privacy failures**. Privacy canaries are also checked in polled partial/progress/error output. Polling can miss short-lived streamed text, so this is not exhaustive stream monitoring. An existing server-side privacy test suite and live audience tests remain required.

`report` exits `0` when this narrow gate passes, `2` when it does not, and `1` on a command error. A passing report is neither an automatic release approval nor evidence that every possible question is safe. Derived-entry usefulness (90%), supported factual claims (95%), original preservation, and all other product acceptance criteria need their own recorded review.
