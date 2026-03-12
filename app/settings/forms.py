import pytz
from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, SelectField, PasswordField
from wtforms.validators import DataRequired, Email, NumberRange, Optional


def timezone_choices():
    common = [
        "Australia/Brisbane",
        "Australia/Sydney",
        "Australia/Melbourne",
        "Australia/Perth",
        "Australia/Adelaide",
        "Australia/Darwin",
        "Pacific/Auckland",
        "Asia/Singapore",
        "UTC",
    ]
    all_tz = pytz.all_timezones
    choices = [(tz, tz) for tz in common if tz in all_tz]
    others = [(tz, tz) for tz in sorted(all_tz) if tz not in common]
    return choices + others


class SettingsForm(FlaskForm):
    timezone = SelectField("Timezone", choices=[], validators=[DataRequired()])
    smtp_email = StringField("Gmail Address", validators=[Optional(), Email()])
    smtp_app_password = PasswordField("Gmail App Password", validators=[Optional()])
    app_name = StringField("App Name", validators=[DataRequired()])
    reminder_hour = IntegerField(
        "Daily Reminder Hour (0-23)",
        validators=[DataRequired(), NumberRange(min=0, max=23)],
    )
    grace_period_days = IntegerField(
        "Late Fee Grace Period (days)",
        validators=[DataRequired(), NumberRange(min=0, max=14)],
    )
    test_email_address = StringField("Send Test Email To", validators=[Optional(), Email()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timezone.choices = timezone_choices()
