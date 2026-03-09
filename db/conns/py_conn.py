"""Python database connection factory.

Reads connection details from db/conns/db.yaml and provides
SQLAlchemy engines for QuestDB (Postgres wire) and PostgreSQL.
"""

import os
import logging

import yaml
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db.yaml")
_engines = {}


def _load_db_config():
    """Load and return the db.yaml config dict."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_questdb_engine():
    """Get or create a SQLAlchemy engine for QuestDB via Postgres wire protocol."""
    if "questdb" not in _engines:
        cfg = _load_db_config()["questdb"]
        url = (
            f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
            f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
        )
        _engines["questdb"] = create_engine(url, pool_pre_ping=True)
        logger.info("Created QuestDB engine (port %s)", cfg["port"])
    return _engines["questdb"]


def get_postgres_engine():
    """Get or create a SQLAlchemy engine for PostgreSQL."""
    if "postgres" not in _engines:
        cfg = _load_db_config()["postgres"]
        url = (
            f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
            f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
        )
        _engines["postgres"] = create_engine(url, pool_pre_ping=True)
        logger.info("Created PostgreSQL engine (port %s)", cfg["port"])
    return _engines["postgres"]


def get_questdb_conn_string():
    """Get a PostgreSQL connection string for QuestDB (used by ConnectorX/Polars)."""
    cfg = _load_db_config()["questdb"]
    return (
        f"postgresql://{cfg['user']}:{cfg['password']}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
    )


def get_postgres_conn_string():
    """Get a PostgreSQL connection string for Postgres (used by ConnectorX/Polars)."""
    cfg = _load_db_config()["postgres"]
    return (
        f"postgresql://{cfg['user']}:{cfg['password']}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
    )


def dispose_all():
    """Dispose all cached engines. Call on shutdown."""
    for name, engine in _engines.items():
        engine.dispose()
        logger.info("Disposed %s engine", name)
    _engines.clear()
