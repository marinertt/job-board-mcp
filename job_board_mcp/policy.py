"""Portable policy and deterministic safety checks, not an LLM fit oracle."""
import json
import os
import re
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_PROFILE_PATH = str(files("job_board_mcp").joinpath("data/profile.example.json"))
RAW_HEADERS = ["Search date", "First seen", "Posting date", "Last seen", "Last checked", "Current status", "Status changed", "Status history", "Search stream", "Company", "Job title", "Location", "Work arrangement", "Source", "Job link", "Tracker disposition", "Notes", "Unique key"]
REGIONAL_HEADERS = ["search_date", "last_checked", "status", "search_stream", "overall_fit", "company", "job_title", "location", "work_arrangement", "source", "job_link", "notes", "unique_key", "my_notes"]
CAREER_HEADERS = ["Date found", "Status", "Company", "Job title", "Location", "Work arrangement", "Schedule", "Employment type", "Starting pay", "Pay fit", "Paid training", "Training / credential path", "Key requirements", "Physical demands", "Veteran preference", "Job link", "Company review summary", "Review sources & confidence", "Accommodation compatibility", "Culture fit", "Culture fit rationale", "Culture risks", "Application priority", "Last checked", "Notes", "Unique key", "overall_fit", "my_notes"]


def load_profile(path: str | None = None) -> dict:
    profile = json.loads(Path(path or os.environ.get("JOB_MONITOR_PROFILE", DEFAULT_PROFILE_PATH)).read_text())
    if not isinstance(profile, dict) or not isinstance(profile.get("regions"), list):
        raise ValueError("Profile must be an object containing a regions list")
    return profile


def assess_exclusions(job: dict, profile: dict) -> list[str]:
    company = job.get("company", "").casefold().strip()
    text = " ".join(str(job.get(k, "")) for k in ("title", "description", "notes", "sector")).casefold()
    reasons = []
    for excluded in profile.get("excluded_companies", []):
        if excluded.casefold() in company:
            reasons.append(f"Excluded company: {excluded}")
    # Only explicit position/industry evidence, not an employer's unrelated portfolio.
    for sector in profile.get("excluded_sectors", []):
        if re.search(rf"\b{re.escape(sector.casefold())}\b", str(job.get("sector", "")).casefold()):
            reasons.append(f"Excluded sector: {sector}")
    if re.search(r"(?:healthcare|healthtech|medtech|pharmaceutical|clinical research) (?:company|provider|customers|clients|industry)", text):
        reasons.append("Position explicitly serves an excluded healthcare sector")
    if re.search(r"(?:support(?:s|ing)?|serv(?:e|es|ing)|client[: ]+|customer[: ]+)\s+(?:the\s+)?(?:dod|dow|department of defense|department of war|u\.?s\.? (?:army|navy|air force|marine corps)|military installation)\b", text):
        reasons.append("Position explicitly serves DoD/DoW or a military organization")
    if company in {"department of defense", "department of war", "u.s. army", "u.s. navy", "u.s. air force", "united states army", "united states navy", "united states air force"}:
        reasons.append("Direct military department employer")
    if re.search(r"(?:require\w*|must (?:hold|have|possess))[^.\n]{0,70}(?:active|current)[^.\n]{0,40}(?:secret|ts/sci|ts sci|top secret|polygraph)", text) or re.search(r"(?:active|current)\s+(?:secret|ts/sci|top secret)[^.\n]{0,35}(?:required|mandatory)", text):
        reasons.append("Explicitly required active security clearance above Public Trust")
    if re.search(r"(?:regular|frequent|daily)[^.\n]{0,30}long[- ]distance driving|long[- ]distance driving[^.\n]{0,30}(?:required|regular)", text):
        reasons.append("Regular long-distance driving")
    if job.get("authorization_eligible") is False:
        reasons.append("Known disqualifying work authorization")
    return reasons


def build_queries(profile: dict, as_of: str | None = None) -> list[dict]:
    today = date.fromisoformat(as_of) if as_of else datetime.now(ZoneInfo(profile.get("timezone", "UTC"))).date()
    since = today - timedelta(days=int(profile.get("discovery_days", 3)))
    queries = []
    for location in profile.get("locations", []):
        for title in profile.get("title_queries", []):
            queries.append({"stream": "data", "location": location, "query": f'{title} {location} after:{since.isoformat()}', "since": since.isoformat(), "until": today.isoformat()})
    for title in profile.get("transition_queries", []):
        for location in ("DC Virginia nearby Maryland Winchester", "NJ eastern PA southern NY", "Albuquerque Rio Rancho NM"):
            queries.append({"stream": "career_transition", "location": location, "query": f'{title} {location} after:{since.isoformat()}', "since": since.isoformat(), "until": today.isoformat()})
    queries.append({"stream": "older_missed", "location": "all configured regions", "query": "Search older missed live postings; do not infer posting dates from updated_at or snippets.", "since": "unbounded", "until": today.isoformat()})
    return queries


def location_matches_region(job: dict, region: str) -> bool:
    loc = str(job.get("location", "")).casefold()
    arrangement = str(job.get("work_arrangement", "")).casefold()
    if region == "Career Transition Jobs":
        return any(location_matches_region(job, x) for x in ("NY NJ PA", "DC VA MD", "Albuquerque"))
    if region == "Fully Remote":
        return bool(re.search(r"\b(?:united states|usa|u\.s\.|us[- ]remote|remote[- ]us)\b", loc + " " + arrangement)) and "remote" in loc + " " + arrangement and not any(x in arrangement for x in ("hybrid", "office attendance", "onsite required"))
    if region == "Albuquerque":
        return bool(re.search(r"albuquerque|rio rancho|bernalillo|los ranchos", loc))
    if region == "DC VA MD":
        return bool(re.search(r"washington,? d\.?c|district of columbia|arlington|alexandria|fairfax|falls church|reston|herndon|ashburn|sterling|winchester|rockville|bethesda|silver spring|college park", loc))
    if region == "NY NJ PA":
        if re.search(r"new york city|nyc|manhattan|brooklyn", loc) and not re.search(r"hybrid|remote", arrangement):
            return False
        return bool(re.search(r"new jersey|\bnj\b|newark|jersey city|princeton|philadelphia|allentown|bethlehem|easton|new york city|nyc|manhattan|brooklyn|westchester|white plains", loc))
    if region == "Canada & International":
        return bool(re.search(r"canada|ontario|toronto|vancouver|montreal|quebec|ottawa|london|united kingdom|germany|france|spain|italy|geneva|switzerland|nairobi|kenya|south africa|uganda|bulgaria|denmark", loc))
    return False
