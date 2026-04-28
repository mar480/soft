from __future__ import annotations

import json

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for

from .services.job_service import create_validation_job, get_job, start_validation_job
from .services.job_service import create_batch_validation_job, start_batch_validation_job
from .services.rule_admin_service import (
    available_rule_families,
    create_draft,
    family_stats,
    get_draft,
    get_family_view,
    publish_draft,
    restore_archived_version,
    save_draft,
    topic_inventory,
)
from .services.validation_service import (
    create_batch_group,
    get_batch_group,
    get_validation_run,
    prepare_submission,
    recent_batch_groups,
    recent_validation_runs,
    stored_source_files,
    synthetic_example_paths,
)


web = Blueprint("web", __name__)


@web.route("/")
def index() -> Response:
    return redirect(url_for("web.validate"))


@web.route("/validate", methods=["GET", "POST"])
def validate() -> str | Response:
    if request.method == "POST":
        try:
            submission = prepare_submission(
                synthetic_path=request.form.get("synthetic_path"),
                stored_source_id=request.form.get("stored_source_id"),
                upload_files=request.files.getlist("upload_files"),
            )
            source_records = submission["source_records"]
            if submission["mode"] == "batch":
                batch_group = create_batch_group(source_records=source_records)
                job_id = create_batch_validation_job(batch_group_id=batch_group["id"])
                start_batch_validation_job(
                    current_app._get_current_object(),
                    job_id=job_id,
                    batch_group_id=batch_group["id"],
                    source_file_ids=[record["id"] for record in source_records],
                )
            else:
                job_id = create_validation_job(source_file_id=source_records[0]["id"])
                start_validation_job(current_app._get_current_object(), job_id=job_id, source_file_id=source_records[0]["id"])
            return redirect(url_for("web.job_progress", job_id=job_id))
        except ValueError as exc:
            flash(str(exc), "error")

    return render_template(
        "validate.html",
        synthetic_examples=synthetic_example_paths(),
        stored_sources=stored_source_files(),
    )


@web.route("/jobs/<job_id>")
def job_progress(job_id: str) -> str:
    job = get_job(job_id)
    if job is None:
        return render_template("not_found.html"), 404
    return render_template("job_progress.html", job=job)


@web.route("/api/jobs/<job_id>")
def job_status(job_id: str) -> Response:
    job = get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404

    payload = {
        "id": job["id"],
        "status": job["status"],
        "progress_percent": job["progress_percent"],
        "progress_message": job["progress_message"],
        "error_message": job["error_message"],
        "source_name": job["source_name"] or job["batch_name"],
        "results_url": (
            url_for("web.batch_reports", batch_group_id=job["batch_group_id"])
            if job["job_type"] == "batch_validation" and job["batch_group_id"]
            else url_for("web.results", run_id=job["validation_run_id"]) if job["validation_run_id"] else None
        ),
    }
    return jsonify(payload)


@web.route("/results/<run_id>")
def results(run_id: str) -> str:
    run = get_validation_run(run_id)
    if run is None:
        return render_template("not_found.html"), 404
    return render_template("results.html", run=run)


@web.route("/reports")
def reports() -> str:
    return render_template(
        "reports.html",
        reports=recent_validation_runs(),
        batches=recent_batch_groups(),
    )


@web.route("/reports/batches/<batch_group_id>")
def batch_reports(batch_group_id: str) -> str:
    batch = get_batch_group(batch_group_id)
    if batch is None:
        return render_template("not_found.html"), 404
    return render_template("batch_reports.html", batch=batch)


@web.route("/reports/<run_id>/download")
def download_report(run_id: str) -> Response:
    run = get_validation_run(run_id)
    if run is None:
        return render_template("not_found.html"), 404
    return send_file(run["html_report_path"], as_attachment=True, download_name=f"{run['report_name']}.html")


@web.route("/admin")
def admin() -> str:
    family_filter = request.args.get("family")
    return render_template(
        "admin_index.html",
        topics=topic_inventory(family_filter=family_filter),
        family_filter=family_filter,
        families=available_rule_families(),
        family_stats=family_stats(),
    )


@web.route("/admin/topics/<topic_id>")
def admin_topic(topic_id: str) -> str:
    family_name = request.args.get("family")
    if not family_name:
        return render_template("not_found.html"), 404
    view = get_family_view(topic_id=topic_id, family_name=family_name)
    if view is None:
        return render_template("not_found.html"), 404
    return render_template("admin_topic.html", view=view)


@web.route("/admin/topics/<topic_id>/drafts", methods=["POST"])
def admin_create_draft(topic_id: str) -> Response:
    family_name = request.form.get("family_name")
    if not family_name:
        flash("Rule family is required to create a draft.", "error")
        return redirect(url_for("web.admin"))
    try:
        draft_id = create_draft(topic_id=topic_id, family_name=family_name)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.admin"))
    flash("Draft created from the published rule family.", "success")
    return redirect(url_for("web.admin_draft", draft_id=draft_id))


@web.route("/admin/drafts/<draft_id>")
def admin_draft(draft_id: str) -> str:
    draft = get_draft(draft_id)
    if draft is None:
        return render_template("not_found.html"), 404
    return render_template("admin_draft.html", draft=draft)


@web.route("/admin/drafts/<draft_id>/save", methods=["POST"])
def admin_save_draft(draft_id: str) -> Response:
    raw_json = request.form.get("draft_json", "")
    try:
        save_draft(draft_id, raw_json)
    except json.JSONDecodeError as exc:
        flash(f"Draft JSON is not valid JSON: {exc.msg}.", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("Draft saved successfully.", "success")
    return redirect(url_for("web.admin_draft", draft_id=draft_id))


@web.route("/admin/drafts/<draft_id>/publish", methods=["POST"])
def admin_publish_draft(draft_id: str) -> Response:
    try:
        publish_draft(draft_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.admin"))
    flash("Draft published and previous production version archived.", "success")
    return redirect(url_for("web.admin"))


@web.route("/admin/archives/<archive_id>/restore", methods=["POST"])
def admin_restore_archive(archive_id: str) -> Response:
    try:
        restore_archived_version(archive_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("web.admin"))
    flash("Archived version restored and current production version archived.", "success")
    return redirect(url_for("web.admin"))


@web.route("/synthetic-lab")
def synthetic_lab() -> str:
    return render_template("placeholder.html", section_name="Synthetic Lab")
