"""Safely prepare PostgreSQL before the WorkPilot API starts."""

from __future__ import annotations

import os
import json
import re
import sys
import time
from uuid import uuid4
from pathlib import Path
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.exc import OperationalError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
MIGRATION_LOCK_ID = 8_077_365_426_119_105_116  # Stable, application-specific int64.
INITIALIZATION_MARKER = "workpilot:initialization-pending:v1:"

# Running this file by path makes Python use its directory as sys.path[0].
# Add the repository root so the existing ``src`` package is importable.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def log(scope: str, message: str) -> None:
    print(f"[{scope}] {message}", flush=True)


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false, got {value!r}")


def database_url() -> URL:
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not configured")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql":
        raise RuntimeError("Automated migration startup supports PostgreSQL only")
    # The project installs psycopg 3. Convert async or unspecified PostgreSQL URLs
    # to its synchronous dialect for inspection and advisory locking.
    if url.drivername in {"postgresql", "postgresql+asyncpg", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+psycopg")
    return url


def wait_for_database(url: URL) -> Any:
    timeout = max(1, int(os.getenv("DB_WAIT_TIMEOUT_SECONDS", "60")))
    deadline = time.monotonic() + timeout
    engine = create_engine(
        url,
        pool_pre_ping=True,
        # psycopg3 auto-prepares statements server-side (_pg3_0, _pg3_1, ...).
        # After a rollback the server can still hold a stale statement name and
        # re-preparing raises DuplicatePreparedStatement. Disable it here so the
        # migration/introspection connection never collides on prepared names.
        connect_args={"prepare_threshold": None},
    )
    log("DB", "Waiting for database...")
    while True:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            log("DB", "Connected")
            return engine
        except OperationalError as exc:
            if time.monotonic() >= deadline:
                engine.dispose()
                raise RuntimeError(
                    f"Database did not become ready within {timeout} seconds"
                ) from exc
            time.sleep(2)


def load_metadata():
    from src.database import Base

    # Keep this list aligned with alembic/env.py. Imports register every mapped
    # table in Base.metadata; model files remain read-only.
    import src.audit.models  # noqa: F401
    import src.auth.models  # noqa: F401
    import src.comments.models  # noqa: F401
    import src.custom_status.models  # noqa: F401
    import src.favorite.models  # noqa: F401
    import src.label.models  # noqa: F401
    import src.organization.models  # noqa: F401
    import src.project.models  # noqa: F401
    import src.public.models  # noqa: F401
    import src.serial.models  # noqa: F401
    import src.sprint.models  # noqa: F401
    import src.task.models  # noqa: F401
    import src.user_story.models  # noqa: F401
    import src.user_story_status.models  # noqa: F401

    return Base.metadata


def include_object(obj, name: str | None, type_: str, reflected: bool, compare_to) -> bool:
    if type_ == "table" and name == "alembic_version":
        return False
    # PostgreSQL cannot reliably reflect the expression text for these GIN
    # indexes. Their migration-owned existence is checked by Alembic history.
    if type_ == "index" and name and name.endswith("_fts"):
        return False
    return True


def primary_key_differences(connection: Connection, metadata) -> list[str]:
    inspector = inspect(connection)
    differences: list[str] = []
    database_tables = set(inspector.get_table_names(schema="public")) - {"alembic_version"}
    for table_name in sorted(set(metadata.tables) & database_tables):
        expected = tuple(column.name for column in metadata.tables[table_name].primary_key.columns)
        actual = tuple(inspector.get_pk_constraint(table_name, schema="public").get("constrained_columns") or ())
        if actual != expected:
            differences.append(
                f"primary key {table_name}: database={actual!r}, models={expected!r}"
            )
    return differences


def ignored_expression_index_differences(connection: Connection, metadata) -> list[str]:
    """Compare server-parsed definitions, avoiding textual SQL formatting differences.

    Expected indexes are parsed on empty, session-local temporary tables. No
    application rows or persistent schema objects are changed by validation.
    """
    inspector = inspect(connection)
    differences: list[str] = []
    database_tables = set(inspector.get_table_names(schema="public"))
    quote = connection.dialect.identifier_preparer.quote

    def signature(schema, name):
        return connection.execute(text("""
            SELECT am.amname, i.indisunique, i.indisvalid, i.indisready,
                   pg_get_expr(i.indpred, i.indrelid) AS predicate,
                   ARRAY(SELECT pg_get_indexdef(i.indexrelid, n, false)
                         FROM generate_series(1, i.indnatts) n) AS expressions,
                   i.indnkeyatts, i.indclass::text, i.indcollation::text,
                   i.indoption::text
            FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            JOIN pg_namespace ns ON ns.oid=c.relnamespace
            JOIN pg_am am ON am.oid=c.relam
            WHERE ns.oid=CASE WHEN :schema='pg_temp' THEN pg_my_temp_schema()
                             ELSE to_regnamespace(:schema)::oid END
              AND c.relname=:name
        """), {"schema": schema, "name": name}).first()

    for table_name, table in sorted(metadata.tables.items()):
        expected = {i.name: i for i in table.indexes if i.name and i.name.endswith("_fts")}
        if not expected or table_name not in database_tables:
            continue
        actual = {
            index["name"]
            for index in inspector.get_indexes(table_name, schema="public")
            if index.get("name") and index["name"].endswith("_fts")
        }
        if actual != set(expected):
            differences.append(f"expression indexes {table_name}: database={sorted(actual)}, models={sorted(expected)}")
        temporary = "wp_validate_" + uuid4().hex
        connection.exec_driver_sql(
            f"CREATE TEMP TABLE {quote(temporary)} (LIKE public.{quote(table_name)}) ON COMMIT DROP"
        )
        try:
            for name in sorted(actual & set(expected)):
                index = expected[name]
                expressions = ", ".join(str(e.compile(dialect=connection.dialect, compile_kwargs={"literal_binds": True}))
                                        for e in index.expressions)
                method = index.dialect_options["postgresql"]["using"] or "btree"
                predicate = index.dialect_options["postgresql"]["where"]
                where = "" if predicate is None else " WHERE " + str(predicate.compile(dialect=connection.dialect, compile_kwargs={"literal_binds": True}))
                expected_name = "wp_index_" + uuid4().hex
                connection.exec_driver_sql(
                    f"CREATE {'UNIQUE ' if index.unique else ''}INDEX {quote(expected_name)} "
                    f"ON pg_temp.{quote(temporary)} USING {quote(method)} ({expressions}){where}"
                )
                if signature("public", name) != signature("pg_temp", expected_name):
                    differences.append(f"expression index definition differs: {table_name}.{name}")
        finally:
            connection.exec_driver_sql(f"DROP TABLE pg_temp.{quote(temporary)}")
    return differences


def sequence_differences(connection: Connection) -> list[str]:
    row = connection.execute(text("""
        SELECT data_type::text, start_value, min_value, max_value,
               increment_by, cycle, cache_size
        FROM pg_sequences WHERE schemaname='public'
          AND sequencename='global_work_item_serial_seq'
    """)).first()
    expected = ("bigint", 1, 1, 9223372036854775807, 1, False, 1)
    if row is None:
        return ["Missing sequence: public.global_work_item_serial_seq"]
    if tuple(row) != expected:
        return [f"Sequence definition differs: global_work_item_serial_seq: {tuple(row)!r}"]
    return []


def _is_legacy_sprint_index_difference(item) -> bool:
    if not isinstance(item, tuple) or len(item) != 2:
        return False
    operation, index = item
    if operation not in {"remove_index", "add_index"}:
        return False
    if getattr(index, "name", None) != "idx_sprint_snapshot_sprint_date":
        return False
    columns = [expression.name for expression in index.expressions]
    expected = ["date"] if operation == "remove_index" else ["sprint_id", "date"]
    return bool(index.unique) and columns == expected


def schema_differences(
    connection: Connection,
    metadata,
    *,
    allow_legacy_sprint_index: bool = False,
) -> list[str]:
    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": metadata,
            "compare_type": True,
            "compare_server_default": True,
            "include_object": include_object,
        },
    )
    generated = compare_metadata(context, metadata)
    if allow_legacy_sprint_index:
        generated = [
            item for item in generated if not _is_legacy_sprint_index_difference(item)
        ]
    differences = [repr(item) for item in generated]
    differences.extend(primary_key_differences(connection, metadata))
    differences.extend(ignored_expression_index_differences(connection, metadata))
    differences.extend(sequence_differences(connection))
    return differences


def require_matching_schema(
    connection: Connection,
    metadata,
    *,
    allow_legacy_sprint_index: bool = False,
) -> None:
    log("VALIDATION", "Comparing database with SQLAlchemy metadata")
    differences = schema_differences(
        connection,
        metadata,
        allow_legacy_sprint_index=allow_legacy_sprint_index,
    )
    if differences:
        log("VALIDATION", f"Found {len(differences)} schema difference(s):")
        for difference in differences:
            print(f"  - {difference}", flush=True)
        raise RuntimeError("Database schema does not match the SQLAlchemy metadata")
    log("VALIDATION", "No schema differences detected")


def alembic_objects() -> tuple[Config, ScriptDirectory, str]:
    config = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"Expected one Alembic head, found: {heads!r}")
    return config, script, heads[0]


def current_revisions(connection: Connection) -> tuple[str, ...]:
    return tuple(MigrationContext.configure(connection).get_current_heads())


def validate_upgrade_path(script: ScriptDirectory, current: str, head: str) -> None:
    if script.get_revision(current) is None:
        raise RuntimeError(f"Database references unknown Alembic revision {current!r}")
    try:
        revisions = list(script.iterate_revisions(head, current))
    except Exception as exc:
        raise RuntimeError(
            f"No valid Alembic upgrade path from {current!r} to {head!r}"
        ) from exc
    if not revisions or revisions[-1].down_revision != current:
        raise RuntimeError(f"Alembic revision {current!r} is not an ancestor of {head!r}")


def run_alembic_check(config: Config) -> None:
    log("MIGRATION", "Running alembic check")
    command.check(config)
    log("VALIDATION", "No new upgrade operations detected")


def reconcile_sprint_snapshot_index(connection: Connection) -> None:
    """Upgrade the retired HEAD's date-only index to the canonical definition.

    The old consolidated history recorded HEAD while creating this index on
    ``date`` alone. Since that revision is already applied, Alembic cannot
    replay the corrected bridge for those databases.
    """
    inspector = inspect(connection)
    if "sprint_snapshots" not in inspector.get_table_names(schema="public"):
        return
    current = next(
        (
            index
            for index in inspector.get_indexes("sprint_snapshots", schema="public")
            if index.get("name") == "idx_sprint_snapshot_sprint_date"
        ),
        None,
    )
    if current is None or current.get("column_names") == ["sprint_id", "date"]:
        return
    if current.get("column_names") != ["date"] or not current.get("unique"):
        raise RuntimeError(
            "Unexpected idx_sprint_snapshot_sprint_date definition; refusing automatic repair"
        )
    duplicate = connection.execute(text("""
        SELECT sprint_id, date, count(*)
        FROM sprint_snapshots
        GROUP BY sprint_id, date
        HAVING count(*) > 1
        LIMIT 1
    """)).first()
    if duplicate:
        raise RuntimeError(
            "Cannot repair idx_sprint_snapshot_sprint_date: duplicate "
            f"(sprint_id, date) values exist: {tuple(duplicate)!r}"
        )
    log(
        "MIGRATION",
        "Replacing legacy date-only sprint snapshot index with (sprint_id, date)",
    )
    connection.exec_driver_sql("DROP INDEX public.idx_sprint_snapshot_sprint_date")
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX idx_sprint_snapshot_sprint_date "
        "ON public.sprint_snapshots (sprint_id, date)"
    )
    connection.commit()


def adopt_legacy_database(connection, metadata, config, allow_migration, *, unknown_revision=False):
    require_matching_schema(
        connection, metadata, allow_legacy_sprint_index=True
    )
    if not allow_migration:
        raise RuntimeError("Legacy database requires Alembic stamping, but AUTO_MIGRATE=false")
    if not env_bool("ALLOW_LEGACY_DB_STAMP", False):
        raise RuntimeError(
            "Schema matches, but ALLOW_LEGACY_DB_STAMP=true is required to adopt this database"
        )
    reconcile_sprint_snapshot_index(connection)
    # The exception is allowed only to perform the known conversion. Require a
    # completely clean comparison before recording Alembic ownership.
    require_matching_schema(connection, metadata)
    log("MIGRATION", "Stamping existing database to HEAD")
    # Purge replaces only version-table rows, allowing Alembic to stamp without
    # resolving a foreign revision. It never executes migration upgrade functions.
    command.stamp(config, "head", purge=unknown_revision)
    run_alembic_check(config)


def run_seeder() -> None:
    import subprocess

    log("SEED", "Running initial database seeder")
    subprocess.run([sys.executable, "-m", "src.seeder"], cwd=PROJECT_ROOT, check=True)
    log("SEED", "Seeder completed")


def verify_seed(connection: Connection) -> None:
    from src.seeder import DEFAULT_PERMISSIONS

    checks = {
        "permissions": ("SELECT count(*) FROM permissions", len(DEFAULT_PERMISSIONS)),
        "countries": ("SELECT count(*) FROM countries", 1),
        "super_admin": (
            """SELECT count(*) FROM users u JOIN roles r ON r.id = u.role_id
               WHERE r.name = 'super_admin' AND r.organization_id IS NULL
                 AND u.deleted_at IS NULL""",
            1,
        ),
    }
    failures = []
    permissions = set(connection.execute(text("SELECT resource, action FROM permissions")).all())
    if not set(DEFAULT_PERMISSIONS).issubset(permissions):
        failures.append("required permission entries are missing")
    seed_file = PROJECT_ROOT / "src/utils/seeds/seed_countries.sql"
    expected_countries = set()
    for line in seed_file.read_text(encoding="utf-8").splitlines():
        values = re.findall(r"'((?:[^']|'')*)'", line)
        if line.lstrip().startswith("('") and len(values) >= 4:
            expected_countries.add(values[2])
    actual_countries = set(connection.execute(text("SELECT iso2 FROM countries")).scalars())
    if not expected_countries or not expected_countries.issubset(actual_countries):
        failures.append("required country entries are missing")
    for name, (statement, minimum) in checks.items():
        count = int(connection.execute(text(statement)).scalar_one())
        if count < minimum:
            failures.append(f"{name}: expected at least {minimum}, found {count}")
    if failures:
        raise RuntimeError("Seed verification failed: " + "; ".join(failures))
    log("SEED", "Seed data verified")


def initialization_comment(connection: Connection):
    return connection.execute(text("SELECT obj_description(to_regclass('public.alembic_version'), 'pg_class')")).scalar()


def set_initialization_comment(connection: Connection, comment) -> None:
    # COMMENT does not accept bind parameters; use SQLAlchemy's string literal
    # renderer to safely quote the marker and any preserved original comment.
    from sqlalchemy import String, literal
    rendered = str(literal(comment, type_=String()).compile(
        dialect=connection.dialect, compile_kwargs={"literal_binds": True}
    ))
    connection.exec_driver_sql("COMMENT ON TABLE public.alembic_version IS " + rendered)
    connection.commit()


def prepare_database(connection: Connection, allow_migration: bool = True) -> None:
    metadata = load_metadata()
    config, script, head = alembic_objects()
    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    application_tables = tables - {"alembic_version"}
    comment = initialization_comment(connection) if "alembic_version" in tables else None
    pending = isinstance(comment, str) and comment.startswith(INITIALIZATION_MARKER)
    original_comment = json.loads(comment[len(INITIALIZATION_MARKER):]) if pending else comment
    # End introspection's transaction before Alembic opens its own connection.
    connection.commit()

    if not application_tables:
        if not allow_migration:
            raise RuntimeError("Empty database requires migration, but AUTO_MIGRATE=false")
        log("MIGRATION", "Empty database detected")
        command.ensure_version(config)
        set_initialization_comment(connection, INITIALIZATION_MARKER + json.dumps(original_comment))
        pending = True
        log("MIGRATION", "Running alembic upgrade head")
        command.upgrade(config, "head")
        run_alembic_check(config)
        run_seeder()
        verify_seed(connection)
    elif "alembic_version" not in tables:
        log("MIGRATION", "Existing database detected")
        log("MIGRATION", "alembic_version not found")
        adopt_legacy_database(connection, metadata, config, allow_migration)
    else:
        revisions = current_revisions(connection)
        if len(revisions) != 1:
            raise RuntimeError(f"Expected one current Alembic revision, found: {revisions!r}")
        current = revisions[0]
        log("MIGRATION", f"Current revision: {current}; head: {head}")
        if current == head:
            if not allow_migration:
                # Validation-only mode must never repair schema.
                pass
            else:
                reconcile_sprint_snapshot_index(connection)
            run_alembic_check(config)
        elif current not in {revision.revision for revision in script.walk_revisions()}:
            log("MIGRATION", f"Unknown revision {current!r}; validating legacy database")
            adopt_legacy_database(
                connection, metadata, config, allow_migration, unknown_revision=True
            )
        else:
            validate_upgrade_path(script, current, head)
            if not allow_migration:
                raise RuntimeError(
                    f"Database is behind Alembic HEAD ({current} -> {head}), but AUTO_MIGRATE=false"
                )
            log("MIGRATION", "Valid upgrade path detected; running alembic upgrade head")
            command.upgrade(config, "head")
            run_alembic_check(config)

    require_matching_schema(connection, metadata)
    final_revisions = current_revisions(connection)
    if final_revisions != (head,):
        raise RuntimeError(
            f"Final Alembic revision validation failed: current={final_revisions!r}, head={head!r}"
        )
    if pending:
        # An existing database is never automatically seeded on retry. Require
        # completion by the operator using the existing idempotent seeder.
        try:
            verify_seed(connection)
        except RuntimeError as exc:
            raise RuntimeError(
                "Initial seeding is incomplete; startup blocked. Run python -m src.seeder "
                "against this database, then retry startup. " + str(exc)
            ) from exc
        set_initialization_comment(connection, original_comment)
    log("VALIDATION", "Database ready")


def main() -> int:
    try:
        allow_migration = env_bool("AUTO_MIGRATE", True)
        if not allow_migration:
            log("MIGRATION", "AUTO_MIGRATE=false; database will be validated without mutation")
        url = database_url()
        engine = wait_for_database(url)
        try:
            with engine.connect() as connection:
                log("MIGRATION", "Acquiring migration lock")
                lock_timeout = max(
                    1, int(os.getenv("MIGRATION_LOCK_TIMEOUT_SECONDS", "120"))
                )
                deadline = time.monotonic() + lock_timeout
                while not connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID},
                ).scalar_one():
                    connection.rollback()
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Timed out waiting for the PostgreSQL migration lock. "
                            "A stale lock may exist on a pooled database connection."
                        )
                    time.sleep(1)
                connection.commit()
                try:
                    log("MIGRATION", "Migration lock acquired")
                    prepare_database(connection, allow_migration=allow_migration)
                    connection.commit()
                except BaseException:
                    # A failed statement leaves PostgreSQL in 'aborted transaction'
                    # mode; roll back first so the finally block can release the
                    # lock, then re-raise the ORIGINAL error (not the misleading
                    # InFailedSqlTransaction from the unlock statement).
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    # Session-level advisory locks are auto-released when the
                    # connection closes, so a failed unlock here is non-fatal.
                    try:
                        log("MIGRATION", "Releasing migration lock")
                        connection.execute(
                            text("SELECT pg_advisory_unlock(:lock_id)"),
                            {"lock_id": MIGRATION_LOCK_ID},
                        )
                        connection.commit()
                    except Exception as exc:
                        log("ERROR", f"Failed to release migration lock: {exc}")
        finally:
            engine.dispose()
        return 0
    except Exception as exc:
        log("ERROR", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
