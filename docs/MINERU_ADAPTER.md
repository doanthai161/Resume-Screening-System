# MinerU parse pipeline

MinerU runs outside this Docker stack. The `worker-parse` container is an
orchestrator and HTTP client; MongoDB remains the source of truth and Redis
Streams only delivers run identifiers.

For a separate Windows installation in this repository, see
[the local MinerU service](../services/mineru/README.md). It uses its own venv
and ONNX models with a llama.cpp VLM for Standard; it does not add MinerU
dependencies to the BE image.

## Configuration

For Docker Compose, configure at least:

```dotenv
PARSE_WORKER_ADAPTER_FACTORY=app.workers.mineru_adapter:create_adapter
MINERU_API_URL=http://host.docker.internal:8001
# MINERU_API_KEY=replace-if-the-service-requires-authentication
MINERU_PDF_TIER=basic
MINERU_FALLBACK_TIER=standard
MINERU_AUTO_OCR=true
PARSE_PREFLIGHT_TIMEOUT_SECONDS=30
```

If MinerU runs in another container, attach both stacks to a private shared
network and use that container's DNS name instead of publishing it publicly.

Start only the parse worker profile:

```bash
docker compose --profile parse up -d --build
```

For local HTTP development, include the override:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile parse up -d --build
```

Do not use the `workers` profile until a screening adapter is configured;
MinerU implements resume parsing, not candidate screening.

## Processing strategy

1. Validate tenant ownership, local path containment, size, MIME type and
   SHA-256 before any provider receives the file.
2. Inspect PDF text pages or extract native DOCX text in a child process with a
   time limit. Worker cancellation kills and reaps this process. Corrupt PDFs
   with unknown page counts fail preflight rather than bypassing the page limit.
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

Even one PDF page without useful text selects OCR. With `MINERU_AUTO_OCR=false`,
such PDFs fail with `pdf_ocr_required`; they cannot succeed with missing pages.
The legacy `PARSE_NATIVE_TEXT_PAGE_RATIO` setting remains readable for compatibility
but no longer permits partial text coverage. Native text is never silently
truncated. DOCX inspection includes body, nested tables, textboxes, headers and
footers in XML order. Native fallback rejects DOCX containing drawings, images,
embedded objects or imported content it cannot fully cover. MinerU Flash still
does not provide an image-in-DOCX OCR guarantee; the supported scan input is PDF.

MinerU 4.0.10 can omit DOCX header/footer text even when the body passes quality
checks. The adapter restores missing margin lines from the validated DOCX
preflight, with the total text limit enforced. It avoids duplicating margin text
already present in visible Markdown/HTML and marks such results with
`parser_version` ending in `+docx-margins`. This supplements actual source text;
it does not infer candidate information. The main body still comes from MinerU.

Quality checks use visible text: image URLs and Markdown syntax cannot make an
empty OCR result pass. `quality_score` and the legacy `confidence_score` contain
a text-quality heuristic, not a calibrated probability that OCR is correct.
Manual review of layout/order and Vietnamese accents is still necessary.

MinerU connection failures, timeouts, HTTP 429 and 5xx responses are retryable.
Unsafe paths, checksum mismatches, encrypted PDFs and rejected documents are
terminal. Repeated transient MinerU failures open a short Redis-backed circuit
breaker to avoid a retry storm.

Malformed provider responses follow the fallback path; broken HTTP transports
are retryable. Upload response bodies are not buffered. Resume completion checks
that the target still exists and is not deleted inside the Mongo transaction.

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

## Real end-to-end acceptance

Use a dedicated test API, Redis, Mongo replica set, worker and MinerU service.
Prepare a test company/candidate and an access token with upload/parse/view
permissions. The commands below create synthetic resume files and parse runs;
they do not delete data. The Mongo URI is used only to read back persisted text.

Generate five fixtures (digital PDF, English scan, Vietnamese scan, mixed PDF,
Vietnamese DOCX). The output directory must be new to avoid replacing files:

```powershell
.\venv\Scripts\python.exe -m scripts.generate_parse_samples --output .tmp/phase2-cv-samples
```

Set `PARSING_TEST_ACCESS_TOKEN` and `PARSING_TEST_MONGODB_URI` locally without
committing them. Use a token for the test environment and a Mongo URI pointing
to that same environment. Then replace the placeholders:

```powershell
.\venv\Scripts\python.exe -m scripts.verify_parse_e2e `
  --api-url http://localhost:8000 --database <test-database> `
  --company-id <test-company-id> --candidate-id <test-candidate-id> `
  --manifest .tmp/phase2-cv-samples/manifest.json --run-id phase2-20261001 `
  --report test-artifacts/parse-e2e.json
```

Reuse `--run-id` if retrying after an uncertain POST outcome; the script keeps
the same idempotency key for the same CV/company/candidate. A new acceptance run
after fixing the service needs a new run ID. If the command exits 2 before any
cases, check the two required environment variables, credentials and Mongo.

Exit 0 requires every case to finish via **MinerU** (native fallback does not
count), with the expected OCR flag and all manifest terms present in Mongo
`parsed_data.raw_text`. The report contains counts/status, not CV text or secrets.
An exit 1 indicates a failed case; timeouts leave the run for normal recovery.
This is a smoke corpus, not an OCR accuracy benchmark: add anonymized multi-column,
rotated, low-resolution and longer CVs with reviewed expected content before
production acceptance. Do not lower thresholds solely to make the corpus pass.
