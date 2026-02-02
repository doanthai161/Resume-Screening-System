# Resume Screening System

A lightweight backend service to parse, evaluate and screen candidate resumes for job requirements. Built with Python and a modular architecture for APIs, services, and models.

## Features
- Upload and parse resumes
- Match resumes against job requirements using configurable matcher logic
- Store screening results and audit logs
- Email OTP and rate-limiting utilities

## Quick Start

Prerequisites:
- Python 3.10+
- Redis (optional, for caching/ratelimiting)

Install dependencies:

```bash
pip install -r requirements.txt
```

Run locally:

```bash
python app/main.py
```

The app exposes HTTP endpoints under `app/api/` (FastAPI or another ASGI server expected).

## Project Layout

- `app/` — application code and API routes
- `app/models/` — ORM/data models
- `app/services/` — business logic (parser, matcher, extracter)
- `app/core/` — core utilities and config (database, redis, security)
- `app/repositories/` — data access layer
- `app/schemas/` — request/response Pydantic schemas
- `uploads/` — uploaded resumes
- `logs/` — application logs

## Configuration
See `app/core/config.py` and `app/core/simple_config.py` for environment-driven settings. Typical env vars:

- `DATABASE_URL` — database connection string
- `REDIS_URL` — Redis connection string (optional)
- `SMTP_*` — email settings for OTP

## Tests
Run unit tests (if present) with your test runner, for example:

```bash
pytest -q
```

## Contributing
Open an issue or submit a PR with a clear description and tests for new behavior.

## License
Add a LICENSE file to specify the project license (e.g., MIT).

---
For details on endpoints and internals, inspect `app/api/` and `app/services/`.
