import os
from flask import Flask

from .db import close_db, init_db
from .errors import register_error_handlers
from .routes import bp


def create_app(config=None):
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config["DATABASE"] = os.environ.get("DATABASE_PATH", "ledger.db")
    if config:
        app.config.update(config)
    init_db(app.config["DATABASE"])
    app.teardown_appcontext(close_db)
    register_error_handlers(app)
    app.register_blueprint(bp)
    return app
