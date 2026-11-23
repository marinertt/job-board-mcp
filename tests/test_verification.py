import unittest
from unittest.mock import AsyncMock, patch

import httpx

from job_board_mcp.verification import deadline_passed, verify_posting


GH = "https://job-boards.greenhouse.io/example/jobs/123"
GH_API = "https://boards-api.greenhouse.io/v1/boards/example/jobs/123?questions=true"
ID = "11111111-1111-1111-1111-111111111111"
LEVER = f"https://jobs.lever.co/example/{ID}"
LEVER_API = f"https://api.lever.co/v0/postings/example/{ID}?mode=json"


def resp(payload=None, *, url=GH_API, status=200, text=None):
    return httpx.Response(status, json=payload if text is None else None, text=text,
                          request=httpx.Request("GET", url))


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_greenhouse_exact_application_schema_is_open(self):
        payload = {"id": 123, "internal_job_id": 5, "questions": [
            {"fields": [{"name": "first_name"}]}, {"fields": [{"name": "resume"}]}]}
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp(payload))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Open")
        self.assertEqual(result.evidence_type, "employer_application")
        self.assertIn("+00:00", result.checked_at)

    async def test_description_alone_is_not_open(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp({"id": 123, "internal_job_id": 5, "content": "Apply now!"}))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Unclear")

    async def test_prospect_post_is_not_open(self):
        payload = {"id": 123, "internal_job_id": None, "questions": [{"fields": [{"name": "first_name"}, {"name": "resume"}]}]}
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp(payload))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Unclear")

    async def test_exact_api_404_is_closed(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp(status=404))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Closed")

    async def test_403_is_unclear_not_closed(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp(status=403))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Unclear")

    async def test_expired_deadline_overrides_displayed_description(self):
        result = await verify_posting({"url": GH, "deadline": "2001-01-01T23:59:59Z"})
        self.assertEqual(result.status, "Closed")
        self.assertEqual(result.evidence_type, "deadline")

    async def test_deadline_from_exact_api_is_closed(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp({"id": 123, "application_deadline": "2001-01-01T23:59:59Z"}))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Closed")

    async def test_wrong_requisition_never_open(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp({"id": 456, "questions": []}))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Unclear")

    async def test_generic_api_redirect_is_closed(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp({}, url="https://boards-api.greenhouse.io/v1/boards/example/jobs"))):
            result = await verify_posting({"url": GH})
        self.assertEqual(result.status, "Closed")

    async def test_unsupported_urls_not_fetched(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock()) as mock:
            result = await verify_posting({"url": "http://127.0.0.1/secrets"})
        self.assertEqual(result.status, "Unclear")
        mock.assert_not_called()

    async def test_lever_needs_actual_exact_application_form(self):
        responses = [resp({"id": ID, "applyUrl": LEVER + "/apply"}, url=LEVER_API),
                     resp(url=LEVER + "/apply", text='<form method="post" id="application-form"><input name="email"><input name="name"><input type="file" name="resume"><button type="submit">Submit</button></form>')]
        with patch("job_board_mcp.verification.safe_get", AsyncMock(side_effect=responses)):
            result = await verify_posting({"url": LEVER})
        self.assertEqual(result.status, "Open")

    async def test_lever_api_apply_link_alone_is_unclear(self):
        responses = [resp({"id": ID, "applyUrl": LEVER + "/apply"}, url=LEVER_API),
                     resp(url=LEVER + "/apply", text='<p>Job description</p><a href="/jobs">Apply</a>')]
        with patch("job_board_mcp.verification.safe_get", AsyncMock(side_effect=responses)):
            result = await verify_posting({"url": LEVER})
        self.assertEqual(result.status, "Unclear")

    async def test_lever_explicit_closure(self):
        responses = [resp({"id": ID, "applyUrl": LEVER + "/apply"}, url=LEVER_API),
                     resp(url=LEVER + "/apply", text="This job is no longer accepting applications")]
        with patch("job_board_mcp.verification.safe_get", AsyncMock(side_effect=responses)):
            result = await verify_posting({"url": LEVER})
        self.assertEqual(result.status, "Closed")

    async def test_lever_generic_apply_redirect_closed(self):
        responses = [resp({"id": ID, "applyUrl": LEVER + "/apply"}, url=LEVER_API),
                     resp(url="https://jobs.lever.co/example", text="Find a job")]
        with patch("job_board_mcp.verification.safe_get", AsyncMock(side_effect=responses)):
            result = await verify_posting({"url": LEVER})
        self.assertEqual(result.status, "Closed")

    async def test_lever_cross_job_apply_is_unclear(self):
        with patch("job_board_mcp.verification.safe_get", AsyncMock(return_value=resp({"id": ID, "applyUrl": "https://jobs.lever.co/example/22222222-2222-2222-2222-222222222222/apply"}, url=LEVER_API))):
            result = await verify_posting({"url": LEVER})
        self.assertEqual(result.status, "Unclear")

    def test_ambiguous_relative_or_timezone_free_clock_not_closed(self):
        self.assertFalse(deadline_passed("3 days ago"))
        self.assertFalse(deadline_passed("2001-01-01T09:00:00"))


if __name__ == "__main__":
    unittest.main()
