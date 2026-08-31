import logging
import os
import tempfile

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("s3.storage")

AWS_REGION = os.getenv("AWS_REGION")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
SIGNED_URL_EXPIRY = int(os.getenv("S3_SIGNED_URL_EXPIRY", "3600"))

_client = None


def getClient():
    global _client
    if _client is None:
        if not S3_BUCKET_NAME:
            raise RuntimeError("S3_BUCKET_NAME is not set")
        _client = boto3.client(
            "s3",
            region_name=AWS_REGION,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )
    return _client


def buildDocumentKey(*, companyId: int, documentId: int, version: int, filename: str) -> str:
    return f"companies/{companyId}/documents/{documentId}/v{version}_{filename}"


def buildUploadKey(filename: str) -> str:
    return f"uploads/{filename}"


def uploadFile(*, localPath: str, key: str, contentType: str | None = None) -> str:
    """Streams a local file to S3 and returns the stored key."""
    extraArgs = {"ContentType": contentType} if contentType else None
    try:
        getClient().upload_file(localPath, S3_BUCKET_NAME, key, ExtraArgs=extraArgs)
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"S3 upload failed for key {key}: {exc}")
        raise RuntimeError("Failed to upload file to storage") from exc
    return key


def generateSignedUrl(key: str, expiresIn: int | None = None) -> str | None:
    if not key:
        return None
    try:
        return getClient().generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET_NAME, "Key": key},
            ExpiresIn=expiresIn or SIGNED_URL_EXPIRY,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"Signed URL generation failed for key {key}: {exc}")
        return None


def downloadToTempFile(key: str) -> str:
    """Downloads an S3 object to a temp file and returns its local path."""
    suffix = os.path.splitext(key)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        localPath = tmp.name
    try:
        getClient().download_file(S3_BUCKET_NAME, key, localPath)
    except (BotoCoreError, ClientError) as exc:
        if os.path.exists(localPath):
            os.remove(localPath)
        logger.error(f"S3 download failed for key {key}: {exc}")
        raise RuntimeError("Failed to download file from storage") from exc
    return localPath


def getObjectSize(key: str) -> int | None:
    try:
        response = getClient().head_object(Bucket=S3_BUCKET_NAME, Key=key)
        return response.get("ContentLength")
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"head_object failed for key {key}: {exc}")
        return None
