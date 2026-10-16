from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_migration_adds_diarization_fields_to_legacy_database(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    test_engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    with test_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE meetings (id INTEGER PRIMARY KEY, title VARCHAR(500) NOT NULL, "
                "transcript TEXT)"
            )
        )
        connection.execute(
            text("INSERT INTO meetings (id, title, transcript) VALUES (1, 'Legacy', 'kept')")
        )
    test_engine.dispose()

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path}")
    command.upgrade(config, "head")

    migrated_engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    try:
        columns = {column["name"] for column in inspect(migrated_engine).get_columns("meetings")}
        with migrated_engine.connect() as connection:
            record = connection.execute(
                text(
                    "SELECT title, transcript, transcript_segments, diarization_status "
                    "FROM meetings WHERE id = 1"
                )
            ).one()
        assert {"transcript_segments", "diarization_status"}.issubset(columns)
        assert record.title == "Legacy"
        assert record.transcript == "kept"
        assert record.transcript_segments == "[]"
        assert record.diarization_status == "not_configured"
    finally:
        migrated_engine.dispose()
