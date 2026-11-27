"""Portable policy and deterministic safety checks, not an LLM fit oracle."""

import json
import os
import re
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_PROFILE_PATH = str(files("job_board_mcp").joinpath("data/profile.example.json"))
RAW_HEADERS = [
    "Search date",
    "First seen",
    "Posting date",
    "Last seen",
    "Last checked",
    "Current status",
    "Status changed",
    "Status history",
    "Search stream",
    "Company",
    "Job title",
    "Location",
    "Work arrangement",
    "Source",
    "Job link",
    "Tracker disposition",
    "Notes",
    "Unique key",
]
REGIONAL_HEADERS = [
    "search_date",
    "last_checked",
    "status",
    "search_stream",
    "overall_fit",
    "company",
    "job_title",
    "location",
    "work_arrangement",
    "source",
    "job_link",
    "notes",
    "unique_key",
    "my_notes",
]
CAREER_HEADERS = [
    "Date found",
    "Status",
    "Company",
    "Job title",
    "Location",
    "Work arrangement",
    "Schedule",
    "Employment type",
    "Starting pay",
    "Pay fit",
    "Paid training",
    "Training / credential path",
    "Key requirements",
    "Physical demands",
    "Veteran preference",
    "Job link",
    "Company review summary",
    "Review sources & confidence",
    "Accommodation compatibility",
    "Culture fit",
    "Culture fit rationale",
    "Culture risks",
    "Application priority",
    "Last checked",
    "Notes",
    "Unique key",
    "overall_fit",
    "my_notes",
]


def load_profile(path: str | None = None) -> dict:
    profile = json.loads(
        Path(path or os.environ.get("JOB_MONITOR_PROFILE", DEFAULT_PROFILE_PATH)).read_text()
    )
    if not isinstance(profile, dict) or not isinstance(profile.get("regions"), list):
        raise ValueError("Profile must be an object containing a regions list")
    profile.pop("active_profile_id", None)
    profile.pop("saved_profiles", None)
    return profile


def assess_exclusions(job: dict, profile: dict) -> list[str]:
    company = job.get("company", "").casefold().strip()
    reasons = []
    for excluded in profile.get("excluded_companies", []):
        if excluded.casefold() in company:
            reasons.append(f"Excluded company: {excluded}")
    for sector in profile.get("excluded_sectors", []):
        if re.search(rf"\b{re.escape(sector.casefold())}\b", str(job.get("sector", "")).casefold()):
            reasons.append(f"Excluded sector: {sector}")
    for rule in profile.get("excluded_position_rules", []):
        if not isinstance(rule, dict) or not rule.get("pattern"):
            continue
        fields = rule.get("fields", ("title", "description", "notes", "sector"))
        text = " ".join(str(job.get(field, "")) for field in fields)
        if re.search(rule["pattern"], text, re.IGNORECASE):
            reasons.append(rule.get("reason", rule.get("name", "Excluded by position rule")))
    if job.get("authorization_eligible") is False:
        reasons.append("Known disqualifying work authorization")
    return reasons


def build_queries(profile: dict, as_of: str | None = None) -> list[dict]:
    today = (
        date.fromisoformat(as_of)
        if as_of
        else datetime.now(ZoneInfo(profile.get("timezone", "UTC"))).date()
    )
    since = today - timedelta(days=int(profile.get("discovery_days", 3)))
    queries = []
    for location in profile.get("locations", []):
        for title in profile.get("title_queries", []):
            queries.append(
                {
                    "stream": "data",
                    "location": location,
                    "query": f"{title} {location} after:{since.isoformat()}",
                    "since": since.isoformat(),
                    "until": today.isoformat(),
                }
            )
    transition_locations = profile.get("transition_locations", [])
    for title in profile.get("transition_queries", []):
        for location in transition_locations:
            queries.append(
                {
                    "stream": "career_transition",
                    "location": location,
                    "query": f"{title} {location} after:{since.isoformat()}",
                    "since": since.isoformat(),
                    "until": today.isoformat(),
                }
            )
    queries.append(
        {
            "stream": "older_missed",
            "location": "all configured regions",
            "query": "Search older missed live postings; do not infer posting dates from updated_at or snippets.",
            "since": "unbounded",
            "until": today.isoformat(),
        }
    )
    return queries


def location_matches_region(job: dict, region: str, profile: dict | None = None) -> bool:
    if profile is None:
        profile = load_profile()
    transition_region = profile.get("career_transition_region")
    if region == transition_region:
        return any(
            location_matches_region(job, candidate, profile)
            for candidate in profile.get("career_transition_regions", [])
        )

    patterns = profile.get("region_location_patterns", {})
    if not isinstance(patterns, dict):
        return False
    rules = patterns.get(region)
    if not isinstance(rules, dict):
        return False
    location = " ".join(str(job.get(field, "")) for field in ("location", "work_arrangement"))
    include_any = rules.get("include_any", [])
    require_all = rules.get("require_all", [])
    exclude_any = rules.get("exclude_any", [])
    return (
        (
            not include_any
            or any(re.search(pattern, location, re.IGNORECASE) for pattern in include_any)
        )
        and all(re.search(pattern, location, re.IGNORECASE) for pattern in require_all)
        and not any(re.search(pattern, location, re.IGNORECASE) for pattern in exclude_any)
    )
