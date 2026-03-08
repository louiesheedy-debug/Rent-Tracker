from flask import render_template, redirect, url_for, flash
from . import bp
from .forms import SettingsForm
from ..models import db, Settings
from ..emails.sender import send_test_email

OWNER_ID = 1


@bp.route("/", methods=["GET", "POST"])
def index():
    settings = Settings.query.filter_by(user_id=OWNER_ID).first()
    if not settings:
        settings = Settings(user_id=OWNER_ID)
        db.session.add(settings)
        db.session.commit()

    form = SettingsForm(obj=settings)

    if form.validate_on_submit():
        # Handle test email before saving
        if form.test_email_address.data:
            success, error = send_test_email(settings, form.test_email_address.data)
            if success:
                flash(f"Test email sent to {form.test_email_address.data}.", "success")
            else:
                flash(f"Test email failed: {error}", "danger")

        settings.timezone = form.timezone.data
        if form.smtp_email.data:
            settings.smtp_email = form.smtp_email.data
        if form.smtp_app_password.data:
            settings.smtp_app_password = form.smtp_app_password.data
        settings.app_name = form.app_name.data
        settings.reminder_hour = form.reminder_hour.data
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("settings.index"))

    return render_template("settings/index.html", form=form, settings=settings)
