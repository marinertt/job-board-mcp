"""Firestore-backed store for the private Cloud Run deployment.

Each job is a document keyed by a stable hash of its canonical unique key. A
transaction protects per-job updates from concurrent Cloud Run instances.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.cloud import firestore

from .models import JobFilters, JobHit, Verification
from .policy import (CAREER_HEADERS, RAW_HEADERS, REGIONAL_HEADERS,
                     assess_exclusions, load_profile, location_matches_region)
from .store import _append, now_iso, parse_time, unique_key

UTC = timezone.utc


class FirestoreJobStore:
    """Persistent store with the same operations used by the MCP server."""

    def __init__(self, project: str | None = None, collection: str = "job_monitor_jobs"):
        self.client = firestore.Client(project=project)
        self.jobs = self.client.collection(collection)

    @staticmethod
    def _doc_id(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _ref(self, key: str):
        return self.jobs.document(self._doc_id(key))

    def _get(self, key: str) -> dict:
        snapshot = self._ref(key).get()
        if not snapshot.exists:
            raise KeyError(f"Unknown job: {key}")
        job = snapshot.to_dict()
        if job.get("unique_key") != key:
            raise RuntimeError("Firestore key collision detected")
        return job

    def _all_jobs(self) -> list[dict]:
        return [snapshot.to_dict() for snapshot in self.jobs.stream()]

    def _update(self, key: str, transform):
        ref = self._ref(key)
        transaction = self.client.transaction()

        @firestore.transactional
        def apply(txn):
            snapshot = ref.get(transaction=txn)
            if not snapshot.exists:
                raise KeyError(f"Unknown job: {key}")
            job = snapshot.to_dict()
            if job.get("unique_key") != key:
                raise RuntimeError("Firestore key collision detected")
            updated = transform(job)
            txn.set(ref, updated)
            return updated

        return apply(transaction)

    def upsert_hits(self, hits: list[JobHit], search_stream: str = "") -> dict:
        added = repeated = 0
        rejected, keys = [], []
        profile = load_profile()
        today = datetime.now(ZoneInfo(profile.get("timezone", "UTC"))).date().isoformat()
        for index, hit in enumerate(hits):
            try:
                key = unique_key(hit)
            except ValueError as exc:
                rejected.append({"index": index, "url": hit.url, "reason": str(exc)})
                continue

            ref = self._ref(key)
            transaction = self.client.transaction()

            @firestore.transactional
            def upsert(txn, *, _ref=ref, _key=key, _hit=hit):
                snapshot = _ref.get(transaction=txn)
                existed = snapshot.exists
                if existed:
                    job = snapshot.to_dict()
                    if job.get("unique_key") != _key:
                        raise RuntimeError("Firestore key collision detected")
                else:
                    job = {"unique_key": _key, "first_seen": today, "search_date": today,
                           "availability": "Unclear", "last_checked": "", "status_changed": today,
                           "status_history": [f"{today}: first observed Unclear."],
                           "application_status": "", "my_notes": "", "region": "",
                           "overall_fit": "possible", "notes": "", "disposition": "Not promoted",
                           "hidden": False, "verification": None, "aliases": [], "career_fields": {}}
                incoming = _hit.model_dump()
                for field in ("description", "source_job_id", "application_url", "deadline", "posting_date_note"):
                    if not incoming.get(field) and job.get(field):
                        incoming.pop(field, None)
                if incoming.get("posting_date") == "Unknown" and job.get("posting_date", "Unknown") != "Unknown":
                    incoming.pop("posting_date")
                job.update(incoming)
                job["url"] = _key if _hit.url else ""
                job["last_seen"] = today
                job["search_stream"] = search_stream or job.get("search_stream", "")
                if _hit.url and _hit.url not in job["aliases"]:
                    job["aliases"].append(_hit.url)
                reasons = assess_exclusions(job, profile)
                job["exclusion_reasons"] = reasons
                if reasons and not job["region"]:
                    job["disposition"] = "Excluded"
                txn.set(_ref, job)
                return existed

            existed = upsert(transaction)
            repeated += int(existed)
            added += int(not existed)
            keys.append(key)
        return {"added": added, "repeated": repeated, "rejected": rejected, "unique_keys": keys}

    def get_job(self, unique_key: str) -> dict:
        return self._get(unique_key)

    def list_jobs(self, filters: JobFilters | None = None) -> list[dict]:
        filters = filters or JobFilters()
        jobs = self._all_jobs()

        def match(job):
            haystack = " ".join(str(job.get(k, "")) for k in ("company", "title", "description", "notes")).casefold()
            return (not filters.query or all(term in haystack for term in filters.query.casefold().split())) \
                and (not filters.location or filters.location.casefold() in job.get("location", "").casefold()) \
                and all(not getattr(filters, field) or job.get(field, "") == getattr(filters, field)
                        for field in ("source_id", "availability", "region", "application_status"))

        jobs = [job for job in jobs if match(job)]
        rank = {"strong": 0, "good": 1, "possible": 2}
        jobs.sort(key=lambda job: (job.get("search_date", ""), -rank.get(job.get("overall_fit"), 2), job["unique_key"]), reverse=True)
        return jobs[:filters.limit]

    def record_verification(self, key: str, verification: Verification) -> dict:
        checked = parse_time(verification.checked_at)
        if checked > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("Verification cannot be future-dated")

        def update(job):
            previous = job["availability"]
            status = verification.status
            if previous == "Closed" and status == "Unclear":
                status = "Closed"
            if status == "Open" and verification.evidence_type != "employer_application":
                status = "Unclear" if previous != "Closed" else "Closed"
            job["last_checked"] = checked.isoformat()
            job["verification"] = verification.model_dump()
            job["verification"]["status"] = status
            job["availability"] = status
            day = checked.date().isoformat()
            if status != previous:
                job["status_changed"] = day
                job["status_history"].append(f"{day}: {previous} → {status}; {verification.reason}")
            if job["region"]:
                if status == "Closed":
                    line, job["hidden"] = f"Posting closed {day}", True
                elif status == "Unclear":
                    line = f"Availability unverified {day}; {verification.reason}"
                    job["hidden"] = job["application_status"] not in {"applied", "interviewing", "phone screen"}
                else:
                    line = f"Posting reopened {day}" if previous != "Open" else f"Availability verified {day}"
                    job["hidden"] = False
                job["notes"] = _append(job["notes"], line)
            return job

        return self._update(key, update)

    def promote(self, key: str, region: str, overall_fit: str = "possible", notes: str = "") -> dict:
        profile = load_profile()
        if region not in profile["regions"]:
            raise ValueError("Unsupported or retired region")
        if overall_fit not in {"strong", "good", "possible"}:
            raise ValueError("Fit must be strong, good or possible")

        def update(job):
            evidence = job.get("verification") or {}
            if job["availability"] != "Open" or evidence.get("evidence_type") != "employer_application":
                raise ValueError("Promotion requires verified current employer application evidence")
            age = datetime.now(UTC) - parse_time(evidence["checked_at"])
            if age > timedelta(hours=profile.get("fresh_verification_hours", 24)) or age < -timedelta(minutes=5):
                raise ValueError("Application verification is stale; check posting again")
            reasons = assess_exclusions(job, profile)
            if reasons:
                raise ValueError("Excluded: " + "; ".join(reasons))
            if not location_matches_region(job, region):
                raise ValueError("Primary eligible location is ambiguous or outside this region")
            if overall_fit == "strong" and not (job.get("technical_fit_evidence") and job.get("culture_fit_evidence")):
                raise ValueError("Strong fit requires explicit technical AND cultural evidence; use good/possible pending review")
            if overall_fit == "good" and not any(term.casefold() in (job.get("description", "") + " " + job.get("title", "")).casefold()
                                                  for term in profile.get("preferred_technologies", [])):
                raise ValueError("Good fit requires supported strength; use possible pending evidence")
            if job["region"] and job["region"] != region:
                raise ValueError("Posting already belongs to another curated region; avoid duplicates")
            job.update(region=region, overall_fit=overall_fit, disposition=region, hidden=False)
            if not job["application_status"]:
                job["application_status"] = "new"
            job["notes"] = _append(job["notes"], notes)
            return job

        return self._update(key, update)

    def recheck_queue(self, today: str | None = None) -> list[dict]:
        now = parse_time(today or now_iso())
        recent = (now - timedelta(days=14)).date().isoformat()
        due = []
        for job in self._all_jobs():
            last = parse_time(job["last_checked"]) if job["last_checked"] else datetime.min.replace(tzinfo=UTC)
            if ((job["region"] and (job["availability"] == "Open" or not job["hidden"]))
                    or job["first_seen"] >= recent or job["last_seen"] >= recent
                    or (job["availability"] in {"Open", "Unclear"} and last <= now - timedelta(days=7))):
                due.append(job)
        priority = {"new": 0, "applied": 1, "interviewing": 1, "phone screen": 1}
        due.sort(key=lambda job: (priority.get(job["application_status"], 2), job.get("last_checked", "")))
        return due

    def export_rows(self, view: str = "raw") -> list[dict]:
        profile = load_profile()
        if view != "raw" and view not in profile["regions"]:
            raise ValueError("Unknown export view")
        jobs = [job for job in self._all_jobs() if view == "raw" or job["region"] == view]
        rows = []
        for job in jobs:
            if view == "raw":
                values = [job["search_date"], job["first_seen"], job.get("posting_date", "Unknown"), job["last_seen"], job["last_checked"], job["availability"], job["status_changed"], "\n".join(job["status_history"]), job.get("search_stream", ""), job["company"], job["title"], job["location"], job["work_arrangement"], job["source_id"], job["url"], job["disposition"], _append(job["notes"], job.get("posting_date_note", "")), job["unique_key"]]
                rows.append(dict(zip(RAW_HEADERS, values)))
            elif view == "Career Transition Jobs":
                row = {header: job.get("career_fields", {}).get(header, "") for header in CAREER_HEADERS}
                row.update({"Date found": job["search_date"], "Status": job["application_status"], "Company": job["company"], "Job title": job["title"], "Location": job["location"], "Work arrangement": job["work_arrangement"], "Job link": job["url"], "Last checked": job["last_checked"], "Notes": job["notes"], "Unique key": job["unique_key"], "overall_fit": job["overall_fit"], "my_notes": job["my_notes"]})
                rows.append(row)
            else:
                values = [job["search_date"], job["last_checked"], job["application_status"], job.get("search_stream", ""), job["overall_fit"], job["company"], job["title"], job["location"], job["work_arrangement"], job["source_id"], job["url"], job["notes"], job["unique_key"], job["my_notes"]]
                rows.append(dict(zip(REGIONAL_HEADERS, values)))
        return rows
