"""
Where uploaded and generated files live.

Map snapshots, generated report PDFs and homepage images were written to the
container's own disk. On Railway, Render, Fly and most other hosts that disk is
recreated on every deploy, so a farm's map would vanish while its database row
survived, and the report would render with a blank space where the field should
be.

This puts a small layer in front of that. Locally, and by default, it still
writes to `uploads/` exactly as before, so nothing changes for development. Set
the S3 variables and the same calls write to object storage instead, where the
files outlive the container.

    S3_BUCKET            required to switch the backend on
    S3_ACCESS_KEY_ID     credentials
    S3_SECRET_ACCESS_KEY
    S3_ENDPOINT_URL      set for Cloudflare R2, DigitalOcean Spaces, MinIO;
                         leave unset for AWS S3
    S3_REGION            default 'auto', which is what R2 expects
    S3_PREFIX            optional key prefix, e.g. 'shamba/'

Cloudflare R2 speaks the S3 API, so it needs no separate backend — only its
endpoint URL.

Names are stored in the database unchanged, so switching backends needs no
migration. What changes is only where the bytes are.
"""
import io
import os
import mimetypes
import shutil
import tempfile
from contextlib import contextmanager

try:
    import boto3
    from botocore.exceptions import ClientError
    BOTO_AVAILABLE = True
except Exception:                       # pragma: no cover - optional dependency
    BOTO_AVAILABLE = False


class LocalStorage:
    """The original behaviour: files on the container's own disk."""

    name = "local"

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key):
        return os.path.join(self.root, key)

    def save_fileobj(self, fileobj, key):
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            shutil.copyfileobj(fileobj, fh)
        return key

    def save_bytes(self, data, key):
        return self.save_fileobj(io.BytesIO(data), key)

    def read(self, key):
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def exists(self, key):
        return os.path.exists(self._path(key))

    def delete(self, key):
        try:
            os.remove(self._path(key))
            return True
        except OSError:
            return False

    @contextmanager
    def local_path(self, key):
        """A real path on disk for code that needs one, such as the renderer."""
        path = self._path(key)
        yield path if os.path.exists(path) else None


class S3Storage:
    """
    Anything speaking the S3 API: AWS S3, Cloudflare R2, Spaces, MinIO.

    The bucket stays private. Files are streamed back through the application
    rather than handed out as presigned URLs, so access follows exactly the same
    rules as before and a link in a report can never expire.
    """

    name = "s3"

    def __init__(self, bucket, prefix="", endpoint_url=None, region="auto",
                 access_key=None, secret_key=None):
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region or "auto",
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
        )

    def _key(self, key):
        return self.prefix + key.lstrip("/")

    def save_fileobj(self, fileobj, key):
        extra = {}
        guessed = mimetypes.guess_type(key)[0]
        if guessed:
            extra["ContentType"] = guessed
        self.client.upload_fileobj(fileobj, self.bucket, self._key(key), ExtraArgs=extra or None)
        return key

    def save_bytes(self, data, key):
        return self.save_fileobj(io.BytesIO(data), key)

    def read(self, key):
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
            return obj["Body"].read()
        except ClientError:
            return None

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError:
            return False

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError:
            return False

    @contextmanager
    def local_path(self, key):
        """
        Download to a temporary file so code that needs a real path still works.

        The renderer inlines images as base64 and reads them from disk, so an
        object has to touch the filesystem briefly. The file is removed as soon
        as the caller is done with it.
        """
        data = self.read(key)
        if data is None:
            yield None
            return
        suffix = os.path.splitext(key)[1] or ""
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            yield path
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def build_storage(local_root):
    """
    Pick a backend from the environment.

    Falling back to local storage rather than failing is deliberate: a missing
    S3 variable should not stop the application booting, and the log line says
    plainly which backend is in use so a misconfiguration is visible at start-up
    rather than the first time somebody uploads a map.
    """
    bucket = os.environ.get("S3_BUCKET", "").strip()
    if not bucket:
        return LocalStorage(local_root)
    if not BOTO_AVAILABLE:
        print("[storage] S3_BUCKET is set but boto3 is not installed — "
              "falling back to local disk. Add boto3 to requirements.txt.")
        return LocalStorage(local_root)
    try:
        store = S3Storage(
            bucket=bucket,
            prefix=os.environ.get("S3_PREFIX", ""),
            endpoint_url=os.environ.get("S3_ENDPOINT_URL", "").strip(),
            region=os.environ.get("S3_REGION", "auto").strip(),
            access_key=os.environ.get("S3_ACCESS_KEY_ID", "").strip(),
            secret_key=os.environ.get("S3_SECRET_ACCESS_KEY", "").strip(),
        )
        print(f"[storage] using S3 bucket '{bucket}'"
              + (f" at {os.environ['S3_ENDPOINT_URL']}" if os.environ.get("S3_ENDPOINT_URL") else ""))
        return store
    except Exception as exc:
        print(f"[storage] could not reach object storage ({exc}) — using local disk.")
        return LocalStorage(local_root)
