from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, DecimalField
from wtforms.fields import DateField
from wtforms.validators import DataRequired, Email, NumberRange, Optional


class TenantForm(FlaskForm):
    full_name = StringField("Full Name", validators=[DataRequired()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    fortnightly_rent = DecimalField(
        "Fortnightly Rent ($)", places=2, validators=[DataRequired(), NumberRange(min=0)]
    )
    lease_start_date = DateField("Lease Start Date", validators=[DataRequired()])
    # Property fields (inline — we create a Property record from this)
    property_address = StringField("Property Address", validators=[Optional()])
    property_suburb = StringField("Suburb", validators=[Optional()])
    property_state = StringField("State", validators=[Optional()])
    property_postcode = StringField("Postcode", validators=[Optional()])
    notes = TextAreaField("Notes", validators=[Optional()])
