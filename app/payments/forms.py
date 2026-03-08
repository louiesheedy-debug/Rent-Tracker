from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from wtforms import IntegerField, SelectField
from wtforms.validators import Optional


class CsvUploadForm(FlaskForm):
    file = FileField(
        "Bank Statement CSV",
        validators=[FileRequired(), FileAllowed(["csv"], "CSV files only.")],
    )
