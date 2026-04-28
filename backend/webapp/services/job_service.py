from __future__ import annotations

import threading
import uuid
from typing import Any

from flask import Flask

from ..db import get_db
from .validation_service import perform_validation_run, utc_now


def create_validation_job(*, source_file_id: str) -> str:
    job_id = uuid.uuid4().hex
    db = get_db()
    db.execute(
        """
        INSERT INTO jobs (
            id,
            job_type,
            status,
            progress_percent,
            progress_message,
            source_file_id,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "validation",
            "queued",
            0,
            "Queued for validation.",
            source_file_id,
            utc_now(),
        ),
    )
    db.commit()
    return job_id


def create_batch_validation_job(*, batch_group_id: str) -> str:
    job_id = uuid.uuid4().hex
    db = get_db()
    db.execute(
        """
        INSERT INTO jobs (
            id,
            job_type,
            status,
            progress_percent,
            progress_message,
            batch_group_id,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "batch_validation",
            "queued",
            0,
            "Queued for batch validation.",
            batch_group_id,
            utc_now(),
        ),
    )
    db.commit()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT
            jobs.*,
            source_files.display_name AS source_name,
            batch_groups.display_name AS batch_name
        FROM jobs
        LEFT JOIN source_files ON source_files.id = jobs.source_file_id
        LEFT JOIN batch_groups ON batch_groups.id = jobs.batch_group_id
        WHERE jobs.id = ?
        """,
        (job_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def start_validation_job(app: Flask, *, job_id: str, source_file_id: str) -> None:
    worker = threading.Thread(
        target=_run_validation_job,
        args=(app, job_id, source_file_id),
        daemon=True,
        name=f"validation-job-{job_id[:8]}",
    )
    worker.start()


def mark_job_started(job_id: str) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE jobs
        SET status = ?, progress_percent = ?, progress_message = ?, started_at = ?
        WHERE id = ?
        """,
        ("running", 5, "Preparing validation job.", utc_now(), job_id),
    )
    db.commit()


def update_job_progress(job_id: str, *, progress_percent: int, progress_message: str) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE jobs
        SET progress_percent = ?, progress_message = ?
        WHERE id = ?
        """,
        (progress_percent, progress_message, job_id),
    )
    db.commit()


def mark_job_completed(job_id: str, *, validation_run_id: str) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE jobs
        SET status = ?, progress_percent = ?, progress_message = ?, validation_run_id = ?, completed_at = ?
        WHERE id = ?
        """,
        ("completed", 100, "Validation complete.", validation_run_id, utc_now(), job_id),
    )
    db.commit()


def mark_batch_job_completed(job_id: str, *, batch_group_id: str) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE jobs
        SET status = ?, progress_percent = ?, progress_message = ?, batch_group_id = ?, completed_at = ?
        WHERE id = ?
        """,
        ("completed", 100, "Batch validation complete.", batch_group_id, utc_now(), job_id),
    )
    db.commit()


def mark_job_failed(job_id: str, *, error_message: str) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE jobs
        SET status = ?, progress_percent = ?, progress_message = ?, error_message = ?, completed_at = ?
        WHERE id = ?
        """,
        ("failed", 100, "Validation failed.", error_message, utc_now(), job_id),
    )
    db.commit()


def _run_validation_job(app: Flask, job_id: str, source_file_id: str) -> None:
    with app.app_context():
        try:
            mark_job_started(job_id)
            run_id = perform_validation_run(
                source_file_id=source_file_id,
                progress_callback=lambda percent, message: update_job_progress(
                    job_id,
                    progress_percent=percent,
                    progress_message=message,
                ),
            )
            mark_job_completed(job_id, validation_run_id=run_id)
        except Exception as exc:
            mark_job_failed(job_id, error_message=str(exc))


def start_batch_validation_job(app: Flask, *, job_id: str, batch_group_id: str, source_file_ids: list[str]) -> None:
    worker = threading.Thread(
        target=_run_batch_validation_job,
        args=(app, job_id, batch_group_id, source_file_ids),
        daemon=True,
        name=f"batch-validation-job-{job_id[:8]}",
    )
    worker.start()


def _run_batch_validation_job(app: Flask, job_id: str, batch_group_id: str, source_file_ids: list[str]) -> None:
    with app.app_context():
        try:
            mark_job_started(job_id)
            total = len(source_file_ids)
            for index, source_file_id in enumerate(source_file_ids, start=1):
                base = int(((index - 1) / total) * 100)
                update_job_progress(
                    job_id,
                    progress_percent=max(5, base),
                    progress_message=f"Validating file {index} of {total}.",
                )
                perform_validation_run(
                    source_file_id=source_file_id,
                    batch_group_id=batch_group_id,
                    progress_callback=lambda percent, message, idx=index, count=total: update_job_progress(
                        job_id,
                        progress_percent=min(99, int((((idx - 1) + (percent / 100)) / count) * 100)),
                        progress_message=f"File {idx} of {count}: {message}",
                    ),
                )
            mark_batch_job_completed(job_id, batch_group_id=batch_group_id)
        except Exception as exc:
            mark_job_failed(job_id, error_message=str(exc))
