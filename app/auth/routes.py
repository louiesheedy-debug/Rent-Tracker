import os
from flask import render_template, redirect, url_for, flash, request, session
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired
from . import bp


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        correct_username = os.environ.get("APP_USERNAME", "admin")
        correct_password = os.environ.get("APP_PASSWORD", "changeme")
        if form.username.data == correct_username and form.password.data == correct_password:
            session["logged_in"] = True
            return redirect(url_for("dashboard.index"))
        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html", form=form)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
