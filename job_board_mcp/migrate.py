"""Explicit local-only migration; never reads or edits Google Sheets remotely."""

import argparse
import json
import os

from .store import JobStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="CSV with the exact original header row first")
    parser.add_argument("--view", default="raw", help="raw or exact curated region name")
    parser.add_argument("--db", default=".local/jobs.sqlite3")
    parser.add_argument("--profile")
    args = parser.parse_args()
    if args.profile:
        os.environ["JOB_MONITOR_PROFILE"] = args.profile
    result = JobStore(args.db).import_csv(args.file, args.view)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
