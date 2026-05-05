from __future__ import annotations

from pathlib import Path

from flask import Flask

from .db import close_db, init_app as init_db_app
from .routes import web


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    project_root = Path(app.root_path).resolve().parents[1]
    app.config.from_mapping(
        SECRET_KEY="local-soft-validation",
        DATABASE=app.instance_path + "/soft_validation_local.db",
        STORAGE_ROOT=app.instance_path,
        UPLOAD_FOLDER=app.instance_path + "/uploads",
        REPORT_FOLDER=app.instance_path + "/reports",
        RULE_SNAPSHOT_FOLDER=app.instance_path + "/rule_snapshots",
        RULE_ADMIN_FOLDER=app.instance_path + "/rule_admin",
        SPLIT_OUTPUT_DIR=str(project_root / "backend/validation_rules/rule_packs/2026/auto/frs102_candidates"),
        TOPICS_FILE=str(project_root / "backend/validation_rules/generated/2026/frs102/topics.json"),
        CONCEPTS_FILE=str(project_root / "backend/validation_rules/generated/2026/frs102/concepts.json"),
        ROLES_FILE=str(project_root / "backend/validation_rules/generated/2026/frs102/roles.json"),
        SYNTHETIC_EXAMPLES_DIR=str(project_root / "backend/validation_rules/generated/2026/frs102/synthetic_examples"),
        TAXONOMY_YEAR=2026,
        TAXONOMY_ENTRYPOINT="FRS 102",
    )

    init_db_app(app)
    app.teardown_appcontext(close_db)
    app.register_blueprint(web)

    return app
