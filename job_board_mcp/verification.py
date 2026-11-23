"""Conservative, read-only availability verification for exact employer postings."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .models import Verification
from .sources import SourceUnavailable, safe_get


_CLOSED = re.compile(r"(?:no longer accepting applications|no longer available|job (?:has been |is )?(?:closed|removed|expired)|position (?:has been |is )?(?:filled|closed)|this job announcement has closed)", re.I)


def _result(status: str, url: str, reason: str, evidence_type: str = "unverified") -> Verification:
    return Verification(status=status, checked_at=datetime.now(timezone.utc).isoformat(),
                        evidence_url=url, reason=reason, evidence_type=evidence_type)


def deadline_passed(value: str, timezone_name: str = "UTC") -> bool:
    """Only explicit ISO deadlines are interpreted; relative wording stays unknown."""
    if not value:
        return False
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            deadline = datetime.combine(date.fromisoformat(value), time.max, ZoneInfo(timezone_name))
        else:
            deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                # Do not invent a timezone for an ambiguous clock-time deadline.
                return False
        return datetime.now(timezone.utc) > deadline
    except (ValueError, KeyError):
        return False


def _identity(url: str) -> tuple[str, str, str, str] | None:
    """Return ATS, employer, exact posting id, region; reject generic pages."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return None
    path = parsed.path.rstrip("/")
    if host in ("job-boards.greenhouse.io", "boards.greenhouse.io"):
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/jobs/(\d+)(?:/application)?", path)
        if match:
            return "greenhouse", match[1], match[2], "global"
    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/([a-fA-F0-9-]{36})(?:/apply)?", path)
        if match:
            return "lever", match[1], match[2], "eu" if host.startswith("jobs.eu.") else "global"
    return None


class _ApplicationParser(HTMLParser):
    """Recognize an application form, not a generic footer 'Apply' link."""
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict] = []
        self.current: dict | None = None
        self.visible: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in ("script", "style"):
            self.ignored += 1
        if tag == "form":
            self.current = {"attrs": values, "fields": set(), "submit": False}
        if self.current is not None:
            if tag in ("input", "textarea", "select"):
                self.current["fields"].add(str(values.get("name") or values.get("id") or "").lower())
            if tag == "input" and values.get("type", "").lower() == "submit":
                self.current["submit"] = True
            if tag == "button" and values.get("type", "submit").lower() == "submit":
                self.current["submit"] = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.current is not None:
            self.forms.append(self.current)
            self.current = None
        if tag in ("script", "style") and self.ignored:
            self.ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.visible.append(data)

    def has_application(self, identifier: str) -> bool:
        for form in self.forms:
            attrs = form["attrs"]
            marker = " ".join(str(attrs.get(k) or "") for k in ("id", "class", "action")).lower()
            fields = form["fields"]
            email = any("email" in name for name in fields)
            candidate = any(any(word in name for word in ("resume", "cv", "fullname", "first_name", "name")) for name in fields)
            explicit = "application" in marker or identifier.lower() in marker
            if email and candidate and explicit and form["submit"] and attrs.get("method", "").lower() == "post":
                return True
        return False


async def verify_posting(job: dict) -> Verification:
    """Never infer Open from a cached description, board card or absent closure text.

    Greenhouse exact-job application questions are an employer-provided application
    schema tied to the live requisition. Lever requires the exact hosted Apply form.
    Only supported ATS domains are fetched; other imported URLs remain Unclear.
    """
    url = str(job.get("url") or job.get("job_link") or "")
    try:
        if deadline_passed(str(job.get("deadline") or ""), str(job.get("deadline_timezone") or "UTC")):
            return _result("Closed", url, "Stated application deadline has passed", "deadline")
        identity = _identity(url)
        if identity is None:
            return _result("Unclear", url, "Unsupported or non-specific posting URL; verify the exact employer application manually")
        ats, employer, identifier, region = identity
        if ats == "greenhouse":
            endpoint = f"https://boards-api.greenhouse.io/v1/boards/{employer}/jobs/{identifier}?questions=true"
        else:
            api_host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
            endpoint = f"https://{api_host}/v0/postings/{employer}/{identifier}?mode=json"
        response = await safe_get(endpoint)
        if response.status_code in (404, 410):
            return _result("Closed", endpoint, "Exact employer requisition was removed (HTTP 404/410)", "employer_requisition_removed")
        if response.status_code != 200:
            return _result("Unclear", endpoint, f"Employer requisition returned HTTP {response.status_code}; acceptance unverified")
        if urlsplit(str(response.url)).path != urlsplit(endpoint).path and identifier not in urlsplit(str(response.url)).path.split("/"):
            return _result("Closed", str(response.url), "Exact employer requisition redirected to a generic/expired board", "generic_redirect")
        try:
            payload = response.json()
        except ValueError:
            return _result("Unclear", endpoint, "Employer requisition response was not JSON")
        if not isinstance(payload, dict) or str(payload.get("id", "")) != identifier:
            return _result("Unclear", endpoint, "Response did not identify the exact employer requisition")
        if ats == "greenhouse":
            if deadline_passed(str(payload.get("application_deadline") or "")):
                return _result("Closed", endpoint, "Exact employer requisition application deadline has passed", "deadline")
            field_names = {
                str(field.get("name", "")).lower()
                for question in payload.get("questions", []) if isinstance(question, dict)
                for field in question.get("fields", []) if isinstance(field, dict)
            }
            # A null internal_job_id is a prospect/general-interest post, not a vacancy.
            if payload.get("internal_job_id") is not None and "first_name" in field_names and any("resume" in n for n in field_names):
                return _result("Open", endpoint, "Exact employer requisition exposes current candidate application fields (name and resume)", "employer_application")
            return _result("Unclear", endpoint, "Job text exists, but no exact current application schema was established")
        apply_url = str(payload.get("applyUrl") or "")
        apply_identity = _identity(apply_url)
        if apply_identity != identity or not urlsplit(apply_url).path.rstrip("/").endswith("/apply"):
            return _result("Unclear", endpoint, "Employer did not provide a matching exact-job Apply URL")
        application = await safe_get(apply_url)
        if application.status_code in (404, 410):
            return _result("Closed", apply_url, "Exact employer application page was removed", "employer_application_removed")
        if application.status_code != 200:
            return _result("Unclear", apply_url, f"Exact Apply page returned HTTP {application.status_code}")
        if _identity(str(application.url)) != identity:
            # Safe redirects are already confined to trusted ATS hosts. If the exact
            # posting becomes a generic employer board, the original link is closed.
            return _result("Closed", str(application.url), "Exact Apply URL redirected away from its requisition to a generic/expired board", "generic_redirect")
        parser = _ApplicationParser()
        parser.feed(application.text)
        visible = " ".join(parser.visible)
        if _CLOSED.search(visible):
            return _result("Closed", apply_url, "Exact employer page explicitly reports closure", "explicit_closure")
        if parser.has_application(identifier):
            return _result("Open", apply_url, "Exact employer Apply page contains candidate fields and submission action", "employer_application")
        return _result("Unclear", apply_url, "Exact page loaded, but a functioning application form could not be established")
    except (SourceUnavailable, httpx.HTTPError, OSError, ValueError) as exc:
        return _result("Unclear", url, f"Application acceptance could not be verified: {type(exc).__name__}: {str(exc)[:200]}")
