"""对象存储：生产用 MinIO，本地开发 / 测试可切到本地目录，接口一致。"""
import io
import mimetypes
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    def get_bytes(self, key: str) -> bytes: ...
    def copy(self, src_key: str, dst_key: str) -> str: ...
    def exists(self, key: str) -> bool: ...


def guess_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError(f"非法存储路径: {key}")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def copy(self, src_key: str, dst_key: str) -> str:
        return self.put_bytes(dst_key, self.get_bytes(src_key))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class MinioStorage:
    def __init__(self):
        from minio import Minio

        s = get_settings()
        self.bucket = s.minio_bucket
        self.client = Minio(s.minio_endpoint, access_key=s.minio_access_key, secret_key=s.minio_secret_key, secure=s.minio_secure)
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> str:
        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data), content_type=content_type or guess_type(key))
        return key

    def get_bytes(self, key: str) -> bytes:
        resp = self.client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def copy(self, src_key: str, dst_key: str) -> str:
        from minio.commonconfig import CopySource

        self.client.copy_object(self.bucket, dst_key, CopySource(self.bucket, src_key))
        return dst_key

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        try:
            self.client.stat_object(self.bucket, key)
            return True
        except S3Error:
            return False


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "minio":
        return MinioStorage()
    return LocalStorage(s.local_storage_dir)
