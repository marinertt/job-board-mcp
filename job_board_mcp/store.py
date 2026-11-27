"""SQLite-backed append-preserving posting audit and curated views.

Availability is system-owned. Application status and my_notes are user-owned.
Discovery, imports and availability checks never update existing user fields.
"""

import csv
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from .models import JobFilters, JobHit, Verification
from .policy import (
    CAREER_HEADERS,
    RAW_HEADERS,
    REGIONAL_HEADERS,
    assess_exclusions,
    load_profile,
    location_matches_region,
)

UTC = timezone.utc
TRACKING = {
    "gh_src",
    "source",
    "ref",
    "referrer",
    "trk",
    "trackingid",
    "lever-source",
    "lever-origin",
    "campaignid",
}
APPLICATION_STATUSES = {"new", "applied", "no", "interviewing", "phone screen"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_time(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        raise ValueError("Posting URL must be an HTTP(S) URL without credentials")
    host = p.hostname.lower()
    if p.port not in (None, 80, 443):
        raise ValueError("Nonstandard ports are not accepted")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.casefold() not in TRACKING and not k.casefold().startswith("utm_")
    ]
    return urlunsplit(
        (p.scheme.lower(), host, p.path.rstrip("/") or "/", urlencode(sorted(query)), "")
    )


def is_specific_url(url: str) -> bool:
    p = urlsplit(url)
    path = p.path.rstrip("/").casefold()
    params = dict(parse_qsl(p.query))
    if any(params.get(k) for k in ("jk", "gh_jid", "jobId", "jobid", "requisitionId")):
        return True
    if path in {"", "/", "/jobs", "/careers", "/jobs/search", "/career", "/search"}:
        return False
    if path.endswith(("/jobs/search", "/careers/search", "/search-jobs")):
        return False
    # Direct employer detail routes need a job-specific segment. No board root.
    if p.hostname and "lever.co" in p.hostname:
        return len([x for x in path.split("/") if x]) >= 2
    if p.hostname and "greenhouse.io" in p.hostname:
        return bool(re.search(r"/jobs/[^/]+", path))
    return bool(
        re.search(
            r"/(?:job|jobs|viewjob|requisition|vacancies|vacancy|positions?|opportunities)/.+", path
        )
        or re.search(r"/[^/]+_jr\d+", path)
    )


def unique_key(hit: JobHit) -> str:
    if hit.url:
        url = canonical_url(hit.url)
        if not is_specific_url(url):
            raise ValueError("Generic career/search pages are not posting audit rows")
        return url
    fields = [
        re.sub(r"\s+", " ", x.strip().casefold()) for x in (hit.company, hit.title, hit.location)
    ]
    if not all(fields):
        raise ValueError("A missing URL requires company, title and location")
    return "|".join(fields)


class JobStore:
    def __init__(self, path: str):
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs (unique_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _save(conn, job):
        conn.execute(
            "INSERT INTO jobs(unique_key,payload) VALUES (?,?) ON CONFLICT(unique_key) DO UPDATE SET payload=excluded.payload",
            (job["unique_key"], json.dumps(job, ensure_ascii=False)),
        )

    @staticmethod
    def _load(conn, key):
        result = conn.execute("SELECT payload FROM jobs WHERE unique_key=?", (key,)).fetchone()
        if not result:
            raise KeyError(f"Unknown job: {key}")
        return json.loads(result[0])

    def upsert_hits(self, hits: list[JobHit], search_stream: str = "") -> dict:
        added, repeated, rejected, keys = 0, 0, [], []
        today = datetime.now(ZoneInfo(load_profile().get("timezone", "UTC"))).date().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for i, hit in enumerate(hits):
                try:
                    key = unique_key(hit)
                except ValueError as exc:
                    rejected.append({"index": i, "url": hit.url, "reason": str(exc)})
                    continue
                try:
                    job = self._load(conn, key)
                except KeyError:
                    job = {
                        "unique_key": key,
                        "first_seen": today,
                        "search_date": today,
                        "availability": "Unclear",
                        "last_checked": "",
                        "status_changed": today,
                        "status_history": [f"{today}: first observed Unclear."],
                        "application_status": "",
                        "my_notes": "",
                        "region": "",
                        "overall_fit": "possible",
                        "notes": "",
                        "disposition": "Not promoted",
                        "hidden": False,
                        "verification": None,
                        "aliases": [],
                        "career_fields": {},
                    }
                    added += 1
                else:
                    repeated += 1
                incoming = hit.model_dump()
                for field in (
                    "description",
                    "source_job_id",
                    "application_url",
                    "deadline",
                    "posting_date_note",
                ):
                    if not incoming.get(field) and job.get(field):
                        incoming.pop(field, None)
                # Preserve known exact posting dates when a provider lacks one later.
                if (
                    incoming.get("posting_date") == "Unknown"
                    and job.get("posting_date", "Unknown") != "Unknown"
                ):
                    incoming.pop("posting_date")
                job.update(incoming)
                job["url"] = key if hit.url else ""
                job["last_seen"] = today
                job["search_stream"] = search_stream or job.get("search_stream", "")
                if hit.url not in job["aliases"] and hit.url:
                    job["aliases"].append(hit.url)
                reasons = assess_exclusions(job, load_profile())
                job["exclusion_reasons"] = reasons
                if reasons and not job["region"]:
                    job["disposition"] = "Excluded"
                self._save(conn, job)
                keys.append(key)
        return {"added": added, "repeated": repeated, "rejected": rejected, "unique_keys": keys}

    def get_job(self, unique_key: str) -> dict:
        with self._connect() as conn:
            return self._load(conn, unique_key)

    def list_jobs(self, filters: JobFilters | None = None) -> list[dict]:
        filters = filters or JobFilters()
        jobs = self._all_jobs()

        def match(j):
            haystack = " ".join(
                str(j.get(k, "")) for k in ("company", "title", "description", "notes")
            ).casefold()
            return (
                (not filters.query or all(x in haystack for x in filters.query.casefold().split()))
                and (
                    not filters.location
                    or filters.location.casefold() in j.get("location", "").casefold()
                )
                and all(
                    not getattr(filters, f) or j.get(k, "") == getattr(filters, f)
                    for f, k in (
                        ("source_id", "source_id"),
                        ("availability", "availability"),
                        ("region", "region"),
                        ("application_status", "application_status"),
                    )
                )
            )

        jobs = [j for j in jobs if match(j)]
        rank = {"strong": 0, "good": 1, "possible": 2}
        jobs.sort(
            key=lambda j: (
                j.get("search_date", ""),
                -rank.get(j.get("overall_fit"), 2),
                j["unique_key"],
            ),
            reverse=True,
        )
        return jobs[: filters.limit]

    def _all_jobs(self) -> list[dict]:
        with self._connect() as conn:
            jobs = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM jobs")]
        rank = {"strong": 0, "good": 1, "possible": 2}
        jobs.sort(
            key=lambda j: (
                j.get("search_date", ""),
                -rank.get(j.get("overall_fit"), 2),
                j["unique_key"],
            ),
            reverse=True,
        )
        return jobs

    def record_verification(self, key: str, verification: Verification) -> dict:
        checked = parse_time(verification.checked_at)
        if checked > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("Verification cannot be future-dated")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._load(conn, key)
            previous = job["availability"]
            # An inaccessible page cannot undo positive closure evidence.
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
                    line = f"Posting closed {day}"
                    job["hidden"] = True
                elif status == "Unclear":
                    line = f"Availability unverified {day}; {verification.reason}"
                    job["hidden"] = job["application_status"] not in {
                        "applied",
                        "interviewing",
                        "phone screen",
                    }
                else:
                    line = (
                        f"Posting reopened {day}"
                        if previous != "Open"
                        else f"Availability verified {day}"
                    )
                    job["hidden"] = False
                job["notes"] = _append(job["notes"], line)
            self._save(conn, job)
            return job

    def promote(
        self, key: str, region: str, overall_fit: str = "possible", notes: str = ""
    ) -> dict:
        profile = load_profile()
        if region not in profile["regions"]:
            raise ValueError("Unsupported or retired region")
        if overall_fit not in {"strong", "good", "possible"}:
            raise ValueError("Fit must be strong, good or possible")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = self._load(conn, key)
            evidence = job.get("verification") or {}
            if (
                job["availability"] != "Open"
                or evidence.get("evidence_type") != "employer_application"
            ):
                raise ValueError(
                    "Promotion requires verified current employer application evidence"
                )
            age = datetime.now(UTC) - parse_time(evidence["checked_at"])
            if age > timedelta(
                hours=profile.get("fresh_verification_hours", 24)
            ) or age < -timedelta(minutes=5):
                raise ValueError("Application verification is stale; check posting again")
            reasons = assess_exclusions(job, profile)
            if reasons:
                raise ValueError("Excluded: " + "; ".join(reasons))
            if not location_matches_region(job, region, profile):
                raise ValueError("Primary eligible location is ambiguous or outside this region")
            if overall_fit == "strong" and not (
                job.get("technical_fit_evidence") and job.get("culture_fit_evidence")
            ):
                raise ValueError(
                    "Strong fit requires explicit technical AND cultural evidence; use good/possible pending review"
                )
            if overall_fit == "good" and not any(
                x.casefold() in (job.get("description", "") + " " + job.get("title", "")).casefold()
                for x in profile.get("preferred_technologies", [])
            ):
                raise ValueError(
                    "Good fit requires supported strength; use possible pending evidence"
                )
            if job["region"] and job["region"] != region:
                raise ValueError(
                    "Posting already belongs to another curated region; avoid duplicates"
                )
            job.update(region=region, overall_fit=overall_fit, disposition=region, hidden=False)
            if not job["application_status"]:
                job["application_status"] = "new"
            job["notes"] = _append(job["notes"], notes)
            self._save(conn, job)
            return job

    def recheck_queue(self, today: str | None = None) -> list[dict]:
        now = parse_time(today or now_iso())
        recent = (now - timedelta(days=14)).date().isoformat()
        due = []
        for job in self._all_jobs():
            last = (
                parse_time(job["last_checked"])
                if job["last_checked"]
                else datetime.min.replace(tzinfo=UTC)
            )
            if (
                (job["region"] and (job["availability"] == "Open" or not job["hidden"]))
                or job["first_seen"] >= recent
                or job["last_seen"] >= recent
                or (job["availability"] in {"Open", "Unclear"} and last <= now - timedelta(days=7))
            ):
                due.append(job)
        priority = {"new": 0, "applied": 1, "interviewing": 1, "phone screen": 1}
        due.sort(
            key=lambda j: (priority.get(j["application_status"], 2), j.get("last_checked", ""))
        )
        return due

    def export_rows(self, view: str = "raw") -> list[dict]:
        profile = load_profile()
        if view != "raw" and view not in profile["regions"]:
            raise ValueError("Unknown export view")
        jobs = [j for j in self._all_jobs() if view == "raw" or j["region"] == view]
        rows = []
        for j in jobs:
            if view == "raw":
                values = [
                    j["search_date"],
                    j["first_seen"],
                    j["posting_date"],
                    j["last_seen"],
                    j["last_checked"],
                    j["availability"],
                    j["status_changed"],
                    "\n".join(j["status_history"]),
                    j["search_stream"],
                    j["company"],
                    j["title"],
                    j["location"],
                    j["work_arrangement"],
                    j["source_id"],
                    j["url"],
                    j["disposition"],
                    _append(j["notes"], j.get("posting_date_note", "")),
                    j["unique_key"],
                ]
                rows.append(dict(zip(RAW_HEADERS, values, strict=True)))
            elif view == "Career Transition Jobs":
                row = {h: j.get("career_fields", {}).get(h, "") for h in CAREER_HEADERS}
                row.update(
                    {
                        "Date found": j["search_date"],
                        "Status": j["application_status"],
                        "Company": j["company"],
                        "Job title": j["title"],
                        "Location": j["location"],
                        "Work arrangement": j["work_arrangement"],
                        "Job link": j["url"],
                        "Last checked": j["last_checked"],
                        "Notes": j["notes"],
                        "Unique key": j["unique_key"],
                        "overall_fit": j["overall_fit"],
                        "my_notes": j["my_notes"],
                    }
                )
                rows.append(row)
            else:
                values = [
                    j["search_date"],
                    j["last_checked"],
                    j["application_status"],
                    j["search_stream"],
                    j["overall_fit"],
                    j["company"],
                    j["title"],
                    j["location"],
                    j["work_arrangement"],
                    j["source_id"],
                    j["url"],
                    j["notes"],
                    j["unique_key"],
                    j["my_notes"],
                ]
                rows.append(dict(zip(REGIONAL_HEADERS, values, strict=True)))
        return rows

    def import_csv(self, path: str, view: str = "raw") -> dict:
        if view != "raw" and view not in load_profile()["regions"]:
            raise ValueError("Unknown import view")
        with Path(path).open(newline="", encoding="utf-8-sig") as file:
            records = list(csv.DictReader(file))
        expected = (
            RAW_HEADERS
            if view == "raw"
            else CAREER_HEADERS
            if view == "Career Transition Jobs"
            else REGIONAL_HEADERS
        )
        if records and list(records[0]) != expected:
            raise ValueError(
                "CSV headers must exactly match the original view schema; remove title rows before import"
            )
        added = repeated = 0
        rejected = []
        for index, row in enumerate(records):
            if view == "raw":
                hit = JobHit(
                    company=row["Company"],
                    title=row["Job title"],
                    location=row["Location"],
                    url=row["Job link"],
                    source_id=row["Source"],
                    posting_date=row["Posting date"] or "Unknown",
                    work_arrangement=row["Work arrangement"],
                )
                status, notes, appstatus, mynotes = row["Current status"], row["Notes"], "", ""
            else:
                career = view == "Career Transition Jobs"
                hit = JobHit(
                    company=row["Company" if career else "company"],
                    title=row["Job title" if career else "job_title"],
                    location=row["Location" if career else "location"],
                    url=row["Job link" if career else "job_link"],
                    source_id="csv_import" if career else row["source"],
                    work_arrangement=row["Work arrangement" if career else "work_arrangement"],
                )
                status, notes = "Unclear", row["Notes" if career else "notes"]
                appstatus, mynotes = row["Status" if career else "status"], row["my_notes"]
            try:
                key = unique_key(hit)
            except ValueError as exc:
                rejected.append({"index": index, "reason": str(exc)})
                continue
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    j = self._load(conn, key)
                    existed = True
                except KeyError:
                    existed = False
            result = self.upsert_hits([hit], "CSV import")
            added += result["added"]
            repeated += result["repeated"]
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                j = self._load(conn, key)
                if not existed:
                    if view == "raw":
                        j["first_seen"] = _normalize_date(row["First seen"]) or j["first_seen"]
                        j["search_date"] = _normalize_date(row["Search date"]) or j["search_date"]
                        j["last_seen"] = _normalize_date(row["Last seen"]) or j["first_seen"]
                        j["status_changed"] = (
                            _normalize_date(row["Status changed"]) or j["search_date"]
                        )
                        j["status_history"] = (
                            [row["Status history"]] if row["Status history"] else []
                        ) + [
                            f"{now_iso()[:10]}: imported historical record; availability requires live verification."
                        ]
                        j["availability"] = "Closed" if status == "Closed" else "Unclear"
                        j["disposition"] = row["Tracker disposition"] or "Not promoted"
                if view != "raw" and (not existed or not j["region"]):
                    # A raw-first migration must still import the matching curated
                    # user decisions once. Never replace already-owned values.
                    j["region"] = view
                    j["disposition"] = view
                    j["search_date"] = (
                        _normalize_date(
                            row["Date found" if view == "Career Transition Jobs" else "search_date"]
                        )
                        or j["search_date"]
                    )
                    if not j["application_status"]:
                        j["application_status"] = appstatus
                    if not j["my_notes"]:
                        j["my_notes"] = mynotes
                    j["overall_fit"] = row.get("overall_fit", "possible")
                    j["hidden"] = j["availability"] == "Closed" or j["application_status"] not in {
                        "applied",
                        "interviewing",
                        "phone screen",
                    }
                    if view == "Career Transition Jobs":
                        j["career_fields"] = row
                # Existing user decisions, notes and curated fields are never overwritten.
                j["notes"] = _append(j["notes"], notes)
                self._save(conn, j)
        return {
            "added": added,
            "repeated": repeated,
            "rejected": rejected,
            "warning": "Imported Open labels are not live verification; recheck before action.",
        }


def _normalize_date(value: str) -> str:
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date: {value}")


def _append(base: str, line: str) -> str:
    if not line or line in (base or ""):
        return base or ""
    return (base + "\n" if base else "") + line
