import csv
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from job_board_mcp.models import JobFilters, JobHit, Verification
from job_board_mcp.policy import (
    RAW_HEADERS,
    REGIONAL_HEADERS,
    CAREER_HEADERS,
    assess_exclusions,
    build_queries,
    load_profile,
    location_matches_region,
)
from job_board_mcp.store import JobStore, canonical_url


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_profile = os.environ.get("JOB_MONITOR_PROFILE")
        self.profile_path = Path(self.tmp.name) / "profile.json"
        profile = load_profile()
        profile["excluded_companies"] = ["Excluded Demo Company"]
        profile["excluded_position_rules"] = [{
            "fields": ["description"],
            "pattern": r"restricted demo contract",
            "reason": "Configured demo rule",
        }]
        self.profile_path.write_text(json.dumps(profile), encoding="utf-8")
        os.environ["JOB_MONITOR_PROFILE"] = str(self.profile_path)
        self.store = JobStore(str(Path(self.tmp.name)/"jobs.db"))
        self.url = "https://job-boards.greenhouse.io/example/jobs/12345"

    def tearDown(self):
        if self.previous_profile is None:
            os.environ.pop("JOB_MONITOR_PROFILE", None)
        else:
            os.environ["JOB_MONITOR_PROFILE"] = self.previous_profile
        self.tmp.cleanup()

    def hit(self, **updates):
        data = dict(company="Example",title="Data Engineer",location="United States",url=self.url,source_id="example",description="Python SQL Spark AWS",work_arrangement="Remote")
        data.update(updates)
        return JobHit(**data)

    def seed(self, **updates):
        return self.store.upsert_hits([self.hit(**updates)])["unique_keys"][0]

    def verify(self, key, status="Open", age=0, evidence="employer_application"):
        return self.store.record_verification(key, Verification(status=status,checked_at=(datetime.now(timezone.utc)-timedelta(hours=age)).isoformat(),evidence_url=self.url,reason="fixture evidence",evidence_type=evidence))

    def write_csv(self, name, headers, row):
        path = Path(self.tmp.name)/name
        with path.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerow(row)
        return str(path)

    def import_applied(self, key):
        row=dict.fromkeys(REGIONAL_HEADERS, "")
        row.update(search_date="2026-11-01",status="applied",company="Example",job_title="Data Engineer",location="United States",work_arrangement="Remote",source="example",job_link=key,unique_key=key,my_notes="Keep my personal note",overall_fit="good")
        return self.store.import_csv(self.write_csv("regional.csv",REGIONAL_HEADERS,row),"Fully Remote")

    def test_raw_capture_includes_excluded_and_weak(self):
        self.seed(company="Excluded Demo Company")
        self.seed(url="https://jobs.lever.co/example/11111111-1111-1111-1111-111111111111",title="Unrelated role")
        self.assertEqual(len(self.store.export_rows()),2)
        self.assertEqual(self.store.get_job(self.url)["disposition"],"Excluded")

    def test_position_exclusions_are_profile_configured(self):
        reasons = assess_exclusions(
            {"description": "Restricted demo contract"},
            load_profile(),
        )
        self.assertEqual(reasons, ["Configured demo rule"])

    def test_generic_pages_rejected(self):
        result=self.store.upsert_hits([self.hit(url="https://example.com/careers"),self.hit(url="https://jobs.lever.co/example")])
        self.assertEqual(result["added"],0)
        self.assertEqual(len(result["rejected"]),2)

    def test_tracking_dedup_alias_and_first_seen(self):
        key=self.seed(posting_date="2026-10-30")
        first=self.store.get_job(key)["first_seen"]
        result=self.store.upsert_hits([self.hit(url=self.url+"?gh_src=campaign",description="")])
        self.assertEqual(result["repeated"],1)
        job=self.store.get_job(key)
        self.assertEqual(job["first_seen"],first)
        self.assertEqual(job["posting_date"],"2026-10-30")
        self.assertIn("Python",job["description"])
        self.assertEqual(len(job["aliases"]),2)

    def test_job_query_id_preserved(self):
        self.assertEqual(canonical_url("https://www.indeed.com/viewjob?jk=abc&utm_source=test"),"https://www.indeed.com/viewjob?jk=abc")

    def test_manual_import_cannot_set_open_or_user_fields(self):
        with self.assertRaises(ValueError):
            self.hit(availability="Open")
        key=self.seed()
        self.assertEqual(self.store.get_job(key)["availability"],"Unclear")

    def test_unverified_promotion_rejected(self):
        key=self.seed()
        with self.assertRaises(ValueError):self.store.promote(key,"Fully Remote")

    def test_promotion_and_closure_preserve_user_fields(self):
        key=self.seed()
        self.import_applied(key)
        self.verify(key)
        self.store.promote(key,"Fully Remote","good")
        self.verify(key,"Closed",evidence="explicit_closure")
        job=self.store.get_job(key)
        self.assertEqual(job["application_status"],"applied")
        self.assertEqual(job["my_notes"],"Keep my personal note")
        self.assertTrue(job["hidden"])
        self.assertIn("Posting closed",job["notes"])

    def test_unclear_applied_stays_visible(self):
        key=self.seed();self.import_applied(key)
        self.verify(key,"Unclear",evidence="unverified")
        self.assertFalse(self.store.get_job(key)["hidden"])

    def test_unclear_new_hidden_then_reopened(self):
        key=self.seed();self.verify(key);self.store.promote(key,"Fully Remote")
        self.verify(key,"Unclear",evidence="unverified")
        self.assertTrue(self.store.get_job(key)["hidden"])
        self.verify(key)
        self.assertFalse(self.store.get_job(key)["hidden"])
        self.assertTrue(any("Unclear → Open" in x for x in self.store.get_job(key)["status_history"]))

    def test_closed_not_undone_by_inaccessibility(self):
        key=self.seed();self.verify(key,"Closed",evidence="explicit_closure")
        self.verify(key,"Unclear",evidence="unverified")
        self.assertEqual(self.store.get_job(key)["availability"],"Closed")

    def test_open_claim_requires_application_evidence(self):
        key=self.seed();self.verify(key,evidence="snippet")
        self.assertEqual(self.store.get_job(key)["availability"],"Unclear")

    def test_stale_check_and_strong_fit_rejected(self):
        key=self.seed();self.verify(key,age=25)
        with self.assertRaises(ValueError):self.store.promote(key,"Fully Remote")
        self.verify(key)
        with self.assertRaises(ValueError):self.store.promote(key,"Fully Remote","strong")

    def test_exclusion_location_and_retired_region_gate(self):
        key=self.seed(company="Excluded Demo Company");self.verify(key)
        with self.assertRaises(ValueError):self.store.promote(key,"Fully Remote")
        other=self.seed(company="Example",url=self.url+"9",location="Canada");self.verify(other)
        with self.assertRaises(ValueError):self.store.promote(other,"Fully Remote")
        with self.assertRaises(ValueError):self.store.promote(other,"Unconfigured Region")

    def test_raw_first_import_then_curated_preserves_status(self):
        raw=dict.fromkeys(RAW_HEADERS,"")
        raw.update({"Search date":"11/01/26","First seen":"10/20/26","Company":"Example","Job title":"Data Engineer","Location":"United States","Work arrangement":"Remote","Source":"example","Job link":self.url,"Unique key":self.url,"Current status":"Open","Posting date":"Unknown"})
        self.store.import_csv(self.write_csv("raw.csv",RAW_HEADERS,raw))
        self.import_applied(self.url)
        job=self.store.get_job(self.url)
        self.assertEqual(job["first_seen"],"2026-10-20")
        self.assertEqual(job["application_status"],"applied")
        self.assertEqual(job["availability"],"Unclear")
        self.assertEqual(job["region"],"Fully Remote")
        self.import_applied(self.url)
        self.assertEqual(self.store.get_job(self.url)["my_notes"],"Keep my personal note")

    def test_recheck_queue_includes_multiple_recent_and_prioritizes_new(self):
        key=self.seed();self.verify(key);self.store.promote(key,"Fully Remote")
        other=self.seed(url=self.url+"9")
        q=self.store.recheck_queue()
        self.assertEqual(len(q),2)
        self.assertEqual(q[0]["unique_key"],key)
        self.assertEqual(JobStore(str(Path(self.tmp.name)/"empty.db")).recheck_queue(),[])

    def test_csv_schema_export(self):
        key=self.seed();self.verify(key);self.store.promote(key,"Fully Remote")
        self.assertEqual(list(self.store.export_rows()[0]),RAW_HEADERS)
        self.assertEqual(list(self.store.export_rows("Fully Remote")[0]),REGIONAL_HEADERS)

    def test_discovery_uses_the_configured_profile(self):
        q=build_queries(load_profile(),"2026-11-23")
        self.assertEqual(q[0]["since"],"2026-11-20")
        data_locations = {item["location"] for item in q if item["stream"] == "data"}
        self.assertEqual(data_locations, set(load_profile()["locations"]))

    def test_region_matching_uses_configured_patterns(self):
        profile = load_profile()
        job = {"location": "Sample City, ZZ", "work_arrangement": "On-site"}
        self.assertTrue(location_matches_region(job, "Example Region", profile))
        self.assertTrue(location_matches_region(job, "Career Transition Jobs", profile))
        self.assertFalse(location_matches_region(job, "Fully Remote", profile))

    def test_transition_discovery_locations_are_configurable(self):
        profile=load_profile()
        profile["transition_locations"]=["Configured transition region"]
        q=build_queries(profile,"2026-11-23")
        transition=[item for item in q if item["stream"]=="career_transition"]
        self.assertTrue(transition)
        self.assertEqual({item["location"] for item in transition},{"Configured transition region"})


if __name__ == "__main__":
    unittest.main()
