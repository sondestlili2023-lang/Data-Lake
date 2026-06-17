import json
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

from app.config import MINIO_ACCESS_KEY, MINIO_ENDPOINT, MINIO_SECRET_KEY

BUCKETS = {
    "raw":     "velib-raw",
    "staging": "velib-staging",
    "curated": "velib-curated",
}

s3_client = boto3.client(
    "s3",
    endpoint_url=f"http://{MINIO_ENDPOINT}",
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
)


def _ensure_bucket(bucket: str) -> None:
    try:
        s3_client.head_bucket(Bucket=bucket)
    except Exception:
        s3_client.create_bucket(Bucket=bucket)


def ensure_all_buckets() -> None:
    for bucket in BUCKETS.values():
        _ensure_bucket(bucket)


def build_key(filename: str) -> str:
    """Timestamped S3 key: YYYY/MM/DD/HHMM/<filename>"""
    now = datetime.now(timezone.utc)
    return f"{now.year}/{now.month:02d}/{now.day:02d}/{now.hour:02d}{now.minute:02d}/{filename}"


def upload_json(bucket: str, key: str, payload: dict[str, Any]) -> None:
    _ensure_bucket(bucket)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        ContentType="application/json",
    )


def upload_bytes(bucket: str, key: str, content: bytes, content_type: str = "application/octet-stream") -> None:
    _ensure_bucket(bucket)
    s3_client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)


def list_objects(bucket: str, prefix: str = "") -> list:
    _ensure_bucket(bucket)
    return s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
