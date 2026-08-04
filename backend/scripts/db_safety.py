"""파괴적/합성 데이터 스크립트가 운영 DB에서 실행되지 않도록 막는 공용 가드입니다."""

from __future__ import annotations

from sqlalchemy.engine import make_url

from backend.app.config import get_app_env


def validate_local_database(database_url: str) -> None:
    """합성 데이터 작업이 로컬 DB에서만 실행되도록 제한합니다."""
    if get_app_env() not in {"local", "development", "test"}:
        raise RuntimeError(
            "This script can only run when APP_ENV is local, development, or test."
        )
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" and url.host not in {
        "127.0.0.1",
        "localhost",
        "mysql",
    }:
        raise RuntimeError("This script can only run against a local database host.")
