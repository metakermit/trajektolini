#!/usr/bin/env python3
"""Upload the generated GTFS zip to Cloudflare R2."""

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

GTFS_ZIP = Path(os.environ.get("GTFS_ZIP_PATH", "gtfs/jadrolinija_gtfs.zip"))
R2_KEY = "jadrolinija_gtfs.zip"


def get_s3_client():
    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def upload():
    if not GTFS_ZIP.exists():
        sys.exit(f"GTFS zip not found at {GTFS_ZIP}. Run gtfs.py first.")

    bucket = os.environ["R2_BUCKET"]
    size_kb = GTFS_ZIP.stat().st_size // 1024
    print(f"Uploading {GTFS_ZIP} ({size_kb} KB) to R2 bucket '{bucket}'...", end=" ", flush=True)

    try:
        s3 = get_s3_client()
        s3.upload_file(str(GTFS_ZIP), bucket, R2_KEY)
        print("OK")
    except (BotoCoreError, ClientError) as e:
        sys.exit(f"Upload failed: {e}")


if __name__ == "__main__":
    upload()
