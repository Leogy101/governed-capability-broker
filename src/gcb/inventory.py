"""SQLite inventory. Identity/research/configuration rows are append-only via this API."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3
from typing import Any

from .models import Backend, Model, Provider
from .research import ResearchFact, SourceCitation, resolve_facts
from .profile_schema import MIGRATION, PROFILE_COLUMNS, PROFILE_REFERENCES
from .validation import ValidationError, canonical, choice, digest, identifier, native_settings

APPLICATION_ID = 0x47434231  # GCB1, checked along with complete table shape before writes.
TABLE_COLUMNS = {
    "providers": ("id", "name"),
    "backends": ("id", "provider_id", "api_family", "revision", "locality", "authorization", "availability", "endpoint_ref"),
    "models": ("id", "backend_id", "native_id", "revision"),
    "research": ("id", "model_id", "property", "value_json", "verification", "sources_json"),
    "configurations": ("id", "model_id", "native_json", "research_ids_json"),
}


@dataclass(frozen=True)
class Configuration:
    id: str
    model_id: str
    native_json: str
    research_ids: tuple[str, ...]

    @property
    def native(self) -> dict:
        return json.loads(self.native_json)


class Inventory:
    """Explicit database only; never scans home directories or provider configuration."""

    def __init__(self, database: str | Path = ":memory:"):
        self.database = str(database)
        new_file = self.database != ":memory:" and not Path(self.database).exists()
        if new_file:
            descriptor = os.open(self.database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        self.db = sqlite3.connect(self.database, timeout=1.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        application = self.db.execute("PRAGMA application_id").fetchone()[0]
        if version not in {0, 1, 2}:
            self.db.close()
            raise ValidationError("unsupported_database_version")
        if version == 0:
            # Refuse to silently turn an unrelated existing database into an inventory.
            if application != 0 or self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                self.db.close()
                raise ValidationError("unrecognized_database")
            self.db.executescript("""
                BEGIN;
                CREATE TABLE providers(id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE backends(id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id),
                    api_family TEXT NOT NULL, revision TEXT NOT NULL, locality TEXT NOT NULL,
                    authorization TEXT NOT NULL, availability TEXT NOT NULL, endpoint_ref TEXT);
                CREATE TABLE models(id TEXT PRIMARY KEY, backend_id TEXT NOT NULL REFERENCES backends(id),
                    native_id TEXT NOT NULL, revision TEXT NOT NULL,
                    UNIQUE(backend_id,native_id,revision));
                CREATE TABLE research(id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id),
                    property TEXT NOT NULL, value_json TEXT NOT NULL, verification TEXT NOT NULL, sources_json TEXT NOT NULL);
                CREATE INDEX research_model ON research(model_id,property);
                CREATE TABLE configurations(id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id),
                    native_json TEXT NOT NULL, research_ids_json TEXT NOT NULL);
                PRAGMA user_version=1;
                PRAGMA application_id=1195590193;
                COMMIT;
            """)
        else:
            try:
                self._check_schema(application, version=version)
            except (ValidationError, sqlite3.Error):
                self.db.close()
                raise
        if version in {0, 1}:
            try:
                self.db.executescript(MIGRATION)
                self._check_schema(APPLICATION_ID, version=2)
            except Exception:
                self.db.rollback()
                self.db.close()
                raise

    def _check_schema(self, application: int, *, version: int = 2) -> None:
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns_by_table = dict(TABLE_COLUMNS)
        if version == 2:
            columns_by_table.update(PROFILE_COLUMNS)
        if application != APPLICATION_ID or tables != set(columns_by_table):
            raise ValidationError("unrecognized_database")
        references = {"backends":("provider_id","providers"), "models":("backend_id","backends"),
                      "research":("model_id","models"), "configurations":("model_id","models")}
        for table, columns in columns_by_table.items():
            details = self.db.execute(f"PRAGMA table_info({table})").fetchall()
            if tuple(row[1] for row in details) != columns or any(row[2] != "TEXT" for row in details):
                raise ValidationError("unrecognized_database_schema")
            if any(row[5] != (1 if row[1] == "id" else 0) for row in details):
                raise ValidationError("unrecognized_database_schema")
            expected_nulls = {"id", "endpoint_ref"}
            if any(row[3] != (0 if row[1] in expected_nulls else 1) for row in details):
                raise ValidationError("unrecognized_database_schema")
            foreign = self.db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            if table in PROFILE_REFERENCES:
                if {(row[3],row[2],row[4]) for row in foreign} != PROFILE_REFERENCES[table]:
                    raise ValidationError("unrecognized_database_schema")
            elif table in references:
                column, parent = references[table]
                if len(foreign) != 1 or (foreign[0][3],foreign[0][2],foreign[0][4]) != (column,parent,"id"):
                    raise ValidationError("unrecognized_database_schema")
            elif foreign:
                raise ValidationError("unrecognized_database_schema")
        model_unique_keys = {tuple(row[2] for row in self.db.execute(f"PRAGMA index_info('{index[1]}')"))
                             for index in self.db.execute("PRAGMA index_list(models)") if index[2]}
        if ("backend_id", "native_id", "revision") not in model_unique_keys:
            raise ValidationError("unrecognized_database_schema")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    def _insert(self, table: str, row: dict[str, Any]) -> None:
        # table/columns are fixed by private callers, never derived from CLI/data.
        existing = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone()
        if existing:
            if dict(existing) != row:
                raise ValidationError("immutable_record_conflict")
            return
        try:
            with self.db:
                self.db.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        except sqlite3.IntegrityError:
            raise ValidationError("inventory_reference_or_identity_conflict") from None

    def add_provider(self, provider: Provider) -> None:
        if not isinstance(provider, Provider):
            raise ValidationError("typed_provider_required")
        self._insert("providers", asdict(provider))

    def add_backend(self, backend: Backend) -> None:
        if not isinstance(backend, Backend):
            raise ValidationError("typed_backend_required")
        self._insert("backends", asdict(backend))

    def add_model(self, model: Model) -> None:
        if not isinstance(model, Model):
            raise ValidationError("typed_model_required")
        self._insert("models", asdict(model))

    def set_backend_state(self, backend_id: str, *, authorization: str, availability: str) -> None:
        self.backend(backend_id)
        choice(authorization, {"allowed", "denied", "unknown"}, "authorization")
        choice(availability, {"ready", "unavailable", "unknown"}, "availability")
        with self.db:
            self.db.execute("UPDATE backends SET authorization=?,availability=? WHERE id=?", (authorization, availability, backend_id))

    def _get(self, table: str, id: str):
        identifier(id)
        row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (id,)).fetchone()
        if row is None:
            raise ValidationError("inventory_record_not_found")
        return dict(row)

    def provider(self, id: str) -> Provider:
        return Provider(**self._get("providers", id))

    def backend(self, id: str) -> Backend:
        return Backend(**self._get("backends", id))

    def model(self, id: str) -> Model:
        return Model(**self._get("models", id))

    def models(self) -> tuple[Model, ...]:
        return tuple(Model(**dict(row)) for row in self.db.execute("SELECT * FROM models ORDER BY id"))

    def add_research(self, fact: ResearchFact) -> str:
        if not isinstance(fact, ResearchFact):
            raise ValidationError("typed_research_required")
        self.model(fact.model_id)
        self._insert("research", {"id":fact.id, "model_id":fact.model_id,"property":fact.property,
                                  "value_json":fact.value_json,"verification":fact.verification,
                                  "sources_json":canonical([asdict(source) for source in fact.sources])})
        return fact.id

    def research(self, model_id: str) -> tuple[ResearchFact, ...]:
        self.model(model_id)
        return tuple(ResearchFact(row["model_id"], row["property"], row["value_json"], row["verification"],
                                  tuple(SourceCitation(**source) for source in json.loads(row["sources_json"])))
                     for row in self.db.execute("SELECT * FROM research WHERE model_id=? ORDER BY property,id", (model_id,)))

    def research_summary(self, model_id: str):
        return resolve_facts(self.research(model_id))

    def research_reasons(self, model_id: str, *, verified: bool = False) -> tuple[str, ...]:
        reasons = []
        for property, fact in self.research_summary(model_id).items():
            if fact.status != "resolved":
                reasons.append(f"research_{property}_{fact.status}")
            elif verified and "verified" not in fact.verifications:
                # Synthetic or merely documented observations never become real verification.
                reasons.append(f"research_{property}_not_verified")
        return tuple(reasons)

    def add_configuration(self, model_id: str, native: dict) -> Configuration:
        model = self.model(model_id)
        native_json = native_settings(native)
        reasons = self.research_reasons(model_id)
        if reasons:
            raise ValidationError("research_required_before_configuration")
        reasoning = self.research_summary(model_id)["reasoning"]
        if native_json not in {canonical(value) for value in reasoning.value["native_configurations"]}:
            raise ValidationError("unresearched_native_configuration")
        all_ids = tuple(sorted(fact.id for fact in self.research(model_id) if fact.verification in {"documented", "verified", "synthetic"}))
        backend = self.backend(model.backend_id)
        backend_identity = {key:value for key,value in asdict(backend).items() if key not in {"authorization","availability"}}
        config_id = digest({"model":asdict(model), "provider":asdict(self.provider(backend.provider_id)),
                            "backend":backend_identity, "native":json.loads(native_json)})
        existing = self.db.execute("SELECT * FROM configurations WHERE id=?", (config_id,)).fetchone()
        if not existing:
            self._insert("configurations", {"id":config_id,"model_id":model_id,"native_json":native_json,
                                            "research_ids_json":canonical(all_ids)})
        return self.configuration(config_id)

    def configuration(self, id: str) -> Configuration:
        row = self._get("configurations", id)
        return Configuration(row["id"], row["model_id"], row["native_json"], tuple(json.loads(row["research_ids_json"])))

    def configurations(self) -> tuple[Configuration, ...]:
        return tuple(Configuration(row["id"],row["model_id"],row["native_json"],tuple(json.loads(row["research_ids_json"])))
                     for row in self.db.execute("SELECT * FROM configurations ORDER BY id"))

    def configuration_reasons(self, config: Configuration, *, verified: bool = False) -> tuple[str, ...]:
        reasons = list(self.research_reasons(config.model_id, verified=verified))
        if not reasons:
            reasoning = self.research_summary(config.model_id)["reasoning"].value
            if config.native_json not in {canonical(value) for value in reasoning["native_configurations"]}:
                reasons.append("native_configuration_no_longer_resolved")
        return tuple(reasons)
