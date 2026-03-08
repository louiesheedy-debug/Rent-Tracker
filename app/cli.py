import click
from .models import db, User, Settings


def register_commands(app):
    @app.cli.command("init-db")
    def init_db():
        """Initialize the database and seed the owner account."""
        with app.app_context():
            db.create_all()
            user = User.query.get(1)
            if not user:
                user = User(id=1, username="owner")
                db.session.add(user)
                db.session.flush()
                db.session.add(Settings(user_id=user.id))
                db.session.commit()
                click.echo("Database initialized and owner account created.")
            else:
                click.echo("Database already initialized.")
