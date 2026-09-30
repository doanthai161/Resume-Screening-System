# MinerU parse pipeline

MinerU runs outside this Docker stack. The `worker-parse` container is an
orchestrator and HTTP client; MongoDB remains the source of truth and Redis
Streams only delivers run identifiers.

## Configuration

For Docker Compose, configure at least:

```dotenv
PARSE_WORKER_ADAPTER_FACTORY=app.workers.mineru_adapter:create_adapter
MINERU_API_URL=http://host.docker.internal:8001
# MINERU_API_KEY=replace-if-the-service-requires-authentication
MINERU_PDF_TIER=basic
MINERU_FALLBACK_TIER=standard
MINERU_AUTO_OCR=true
```

If MinerU runs in another container, attach both stacks to a private shared
network and use that container's DNS name instead of publishing it publicly.

Start only the parse worker profile:

```bash
docker compose --profile parse up -d --build
```

Do not use the `workers` profile until a screening adapter is configured;
MinerU implements resume parsing, not candidate screening.

## Processing strategy

1. Validate tenant ownership, local path containment, size, MIME type and
   SHA-256 before any provider receives the file.
2. Inspect PDF text pages or extract native DOCX text.
3. Use MinerU `txt` mode for digital PDFs and `ocr` mode for scanned/mixed PDFs.
4. Validate output length, alphanumeric signal and replacement-character rate.
5. If output quality is low, retry PDF parsing in OCR mode with the configured
   fallback tier.
6. If MinerU is temporarily unavailable, use native PDF/DOCX text when it
   passes the same quality gate. PDF fallback additionally requires useful
   native text on every page and no OCR requirement. Mixed or unknown-coverage
   PDFs remain retryable on provider outages instead of completing with missing
   pages. This conservative check can reject sparse or blank pages for fallback;
   those documents continue through MinerU.
7. Store each provider invocation in `resume_parse_attempts`; store the selected
   provider, OCR flag, score and fallback reason on `resume_parse_runs`.

MinerU connection failures, timeouts, HTTP 429 and 5xx responses are retryable.
Unsafe paths, checksum mismatches, encrypted PDFs and rejected documents are
terminal. Repeated transient MinerU failures open a short Redis-backed circuit
breaker to avoid a retry storm.

Parse retries are scheduled durably with `next_retry_at`. Provider failures use
exponential backoff (5 seconds initially, capped at 300 seconds); maintenance
publishes due runs on its next tick. Claiming and post-commit publication both
honor this timestamp, including duplicate deliveries and worker restarts.
When the circuit is already open and native fallback is unusable, the run waits
for `MINERU_CIRCUIT_OPEN_SECONDS` without consuming its provider retry budget.
`attempt` remains a monotonically increasing lease generation; the budget used
is `attempt - deferred_attempts`. This preserves stale-worker fencing and unique
parse-attempt history. Existing runs without the new fields remain readable.

A 404 for a previously created upload, polled job, or output file is reported as
`mineru_resource_lost`: the next scheduled attempt starts a fresh upload/job
cycle, bounded by `max_attempts`. A 404 at a create endpoint remains terminal
because it can indicate an incorrect API URL or incompatible deployment.

Worker containers run as the image's non-root user with all Linux capabilities
dropped, a read-only root filesystem and a bounded temporary filesystem. The
resume volume is mounted read-only.

## Status polling

Create a run with:

```http
POST /api/v1/resumes/{resume_id}/parse-runs
Idempotency-Key: <unique-value>
```

Poll it with:

```http
GET /api/v1/resumes/{resume_id}/parse-runs/{run_id}
```

The response exposes `status`, `attempt`, `deferred_attempts`, `next_retry_at`, `error_code`, `final_provider`,
`ocr_used`, `quality_score` and `fallback_reason`. Raw provider exceptions and
credentials are never exposed through this endpoint.

## Scope

This pipeline produces validated Markdown in `parsed_data.raw_text`. Extracting
skills, experience, education and other structured CV fields remains a separate
downstream service/worker concern.
