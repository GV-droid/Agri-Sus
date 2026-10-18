#!/usr/bin/env python3
"""Let the Cloud Run service account read and write the Agri Sus BigQuery dataset, and nothing else.

`bq add-iam-policy-binding` does not support datasets, so this adds the account to the dataset's access list
(WRITER = roles/bigquery.dataEditor on this dataset only). Safe to re-run.
usage: python deploy/grant_dataset_access.py --project P --dataset agri_sus_mvp --member SA_EMAIL
"""
from __future__ import annotations

import argparse

from google.cloud import bigquery

WRITE_ROLES = {"WRITER", "OWNER", "roles/bigquery.dataEditor", "roles/bigquery.dataOwner"}


def grant(client, project: str, dataset: str, member: str) -> bool:
    """Add `member` as a dataset WRITER. Returns False if it already had write access."""
    ds = client.get_dataset(f"{project}.{dataset}")
    entries = list(ds.access_entries)
    if any(e.entity_id == member and e.role in WRITE_ROLES for e in entries):
        return False
    entries.append(bigquery.AccessEntry(role="WRITER", entity_type="userByEmail", entity_id=member))
    ds.access_entries = entries
    client.update_dataset(ds, ["access_entries"])
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--member", required=True, help="service account e-mail")
    a = ap.parse_args()
    added = grant(bigquery.Client(project=a.project), a.project, a.dataset, a.member)
    print(f"    {a.member} {'can now read and write' if added else 'already has access to'} {a.project}.{a.dataset}")


if __name__ == "__main__":
    main()
