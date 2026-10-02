"""Verify a running TEST API -> Redis worker -> MinerU -> Mongo pipeline.

Creates synthetic resumes/runs through the API; Mongo access is read-only.
Never prints access tokens, connection strings, provider bodies or CV text.
"""

import argparse
import asyncio
import hashlib
import json
import os
import time
import unicodedata
from pathlib import Path

import httpx
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient


class AcceptanceError(Exception):
    """Only fixed, locally defined failure codes may be persisted."""


def normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def api_data(response: httpx.Response) -> dict:
    if not response.is_success:
        raise AcceptanceError(f"api_http_{response.status_code}")
    body = response.json()
    if (
        not isinstance(body, dict)
        or body.get("success") is not True
        or not isinstance(body.get("data"), dict)
    ):
        raise AcceptanceError("api_invalid_response")
    return body["data"]


async def verify(args) -> list[dict]:
    token = os.environ.get("PARSING_TEST_ACCESS_TOKEN")
    uri = os.environ.get("PARSING_TEST_MONGODB_URI")
    if not token or not uri:
        raise RuntimeError("Set PARSING_TEST_ACCESS_TOKEN and PARSING_TEST_MONGODB_URI")
    for value in (args.company_id, args.candidate_id):
        if not ObjectId.is_valid(value):
            raise ValueError("Company/candidate IDs must be valid ObjectIds")
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    results = []
    try:
        await mongo.admin.command("ping")
        async with httpx.AsyncClient(
            base_url=args.api_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
            follow_redirects=False,
        ) as api:
            for index, case in enumerate(cases):
                result = {"case": index + 1, "passed": False}
                results.append(result)
                try:
                    path = (args.manifest.parent / case["file"]).resolve(strict=True)
                    if not path.is_relative_to(
                        args.manifest.parent.resolve()
                    ) or path.suffix not in {".pdf", ".docx"}:
                        raise ValueError("Invalid manifest file path")
                    content = path.read_bytes()
                    mime = (
                        "application/pdf"
                        if path.suffix == ".pdf"
                        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
                    resume = api_data(
                        await api.post(
                            "api/v1/resumes",
                            data={
                                "company_id": args.company_id,
                                "candidate_id": args.candidate_id,
                            },
                            files={
                                "file": (
                                    f"synthetic-{index + 1}{path.suffix}",
                                    content,
                                    mime,
                                )
                            },
                        )
                    )
                    # Stable across a retry of this command, including an unknown
                    # POST outcome. Change run-id only for a new acceptance run.
                    key = hashlib.sha256(
                        args.run_id.encode()
                        + content
                        + args.company_id.encode()
                        + args.candidate_id.encode()
                    ).hexdigest()
                    run_path = f"api/v1/resumes/{resume['id']}/parse-runs"
                    run = api_data(
                        await api.post(
                            run_path,
                            headers={"Idempotency-Key": key},
                            json={
                                "company_id": args.company_id,
                                "parser_version": "phase2-acceptance-v1",
                            },
                        )
                    )
                    deadline = time.monotonic() + args.timeout
                    while run["status"] in {"queued", "running"}:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("parse_acceptance_timeout")
                        await asyncio.sleep(3)
                        run = api_data(await api.get(f"{run_path}/{run['id']}"))
                    result.update(
                        status=run["status"],
                        provider=run.get("final_provider"),
                        ocr_used=run.get("ocr_used"),
                        attempts=run.get("attempt"),
                    )
                    if (
                        run["status"] != "completed"
                        or run.get("final_provider") != "mineru"
                    ):
                        raise AcceptanceError("mineru_completion_required")
                    if run.get("ocr_used") != case["expected_ocr"]:
                        raise AcceptanceError("unexpected_ocr_mode")
                    stored = await mongo[args.database].resume_files.find_one(
                        {
                            "_id": ObjectId(resume["id"]),
                            "company_id": ObjectId(args.company_id),
                            "is_deleted": False,
                        }
                    )
                    persisted_run = await mongo[
                        args.database
                    ].resume_parse_runs.find_one(
                        {
                            "_id": ObjectId(run["id"]),
                            "company_id": ObjectId(args.company_id),
                            "resume_file_id": ObjectId(resume["id"]),
                            "status": "completed",
                        }
                    )
                    if (
                        not stored
                        or not persisted_run
                        or stored.get("processed_at")
                        != persisted_run.get("finished_at")
                    ):
                        raise AcceptanceError("persisted_result_does_not_match_run")
                    text = (stored or {}).get("parsed_data", {}).get("raw_text", "")
                    terms = case.get("expected_terms", [])
                    if (
                        not terms
                        or not stored
                        or stored.get("status") != "parsed"
                        or not text
                    ):
                        raise AcceptanceError("persisted_text_missing")
                    matched = sum(
                        normalized(term) in normalized(text) for term in terms
                    )
                    result.update(
                        expected_terms=len(terms),
                        matched_terms=matched,
                        characters=len(text),
                    )
                    if matched != len(terms):
                        raise AcceptanceError("expected_content_missing")
                    result["passed"] = True
                except Exception as exc:
                    # Only our fixed codes are reportable; never repr(provider errors).
                    result["error"] = (
                        str(exc)
                        if isinstance(exc, AcceptanceError)
                        else type(exc).__name__
                    )
    finally:
        mongo.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument(
        "--database", required=True, help="Name of the isolated test database"
    )
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--run-id", required=True, help="Reuse this ID when retrying the same command"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    try:
        results = asyncio.run(verify(args))
    except Exception as exc:
        print(
            f"Acceptance could not start ({type(exc).__name__}); check test configuration."
        )
        raise SystemExit(2) from None
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = sum(item["passed"] for item in results)
    print(f"Parsing acceptance: {passed}/{len(results)} passed; report written.")
    raise SystemExit(0 if results and passed == len(results) else 1)


if __name__ == "__main__":
    main()
