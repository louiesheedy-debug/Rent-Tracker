import os
from flask import Flask
from .models import db
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object("config.Config")

    db.init_app(app)
    csrf.init_app(app)

    # Register blueprints
    from .dashboard import bp as dashboard_bp
    from .tenants import bp as tenants_bp
    from .payments import bp as payments_bp
    from .emails import bp as emails_bp
    from .settings import bp as settings_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(tenants_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(emails_bp)
    app.register_blueprint(settings_bp)

    with app.app_context():
        db.create_all()
        _migrate_db()
        _seed_owner()

    # Register CLI commands
    from .cli import register_commands
    register_commands(app)

    # Start background scheduler
    _start_scheduler(app)

    # Register error handlers
    _register_error_handlers(app)

    return app


def _migrate_db():
    """Safely add new columns to existing tables that db.create_all() won't alter."""
    with db.engine.connect() as conn:
        try:
            conn.execute(db.text(
                "ALTER TABLE rent_periods ADD COLUMN late_fee NUMERIC(10,2) NOT NULL DEFAULT 0"
            ))
            conn.commit()
        except Exception:
            pass  # Column already exists
        try:
            conn.execute(db.text(
                "ALTER TABLE rent_periods ADD COLUMN paid_on_time BOOLEAN"
            ))
            conn.commit()
        except Exception:
            pass  # Column already exists
        try:
            conn.execute(db.text(
                "ALTER TABLE settings ADD COLUMN grace_period_days INTEGER NOT NULL DEFAULT 2"
            ))
            conn.commit()
        except Exception:
            pass  # Column already exists


def _seed_owner():
    """Ensure the single owner account (id=1) exists."""
    from .models import User, Settings
    user = User.query.get(1)
    if not user:
        user = User(id=1, username="owner")
        db.session.add(user)
        db.session.flush()
    if not user.settings:
        db.session.add(Settings(user_id=user.id))
    db.session.commit()


def _start_scheduler(app):
    from .emails.scheduler import start_scheduler
    start_scheduler(app)


def _register_error_handlers(app):
    from flask import render_template

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500
