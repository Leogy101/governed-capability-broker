BEGIN TRANSACTION;
CREATE TABLE backends(id TEXT PRIMARY KEY, provider_id TEXT NOT NULL REFERENCES providers(id),
                    api_family TEXT NOT NULL, revision TEXT NOT NULL, locality TEXT NOT NULL,
                    authorization TEXT NOT NULL, availability TEXT NOT NULL, endpoint_ref TEXT);
INSERT INTO "backends" VALUES('synthetic-local-v1','synthetic','synthetic','1','local','allowed','ready',NULL);
CREATE TABLE configurations(id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id),
                    native_json TEXT NOT NULL, research_ids_json TEXT NOT NULL);
INSERT INTO "configurations" VALUES('4c0b71da95f39e993d0119c9a10cad9f75697158dfd6e0ad7b5e9ed896c520c5','fixture','{"reasoning_effort":"low"}','["6fb5d09327338227e757fc0a9819490bfb657187e365f198b33f3fb7a900e29c","cfc246d150bee593eb17355d080bde0215c46ad70fd8dfcae101695fc60f580f","d37da6a74fdcf3a527380a0fb395e0d8e287702aed030c4d082e8805378fdda8","d9295850fe76ac14051f62ddd64da79c34928adcb93df57d7be16d08524bac1c","fefc80a61783ee67626677d0474684b679e868c345c215775490dc270d74a6aa"]');
INSERT INTO "configurations" VALUES('086631fe9786bbb28a6a225b408dc67ea2b53c04849fa0e34b3074da9274409d','fixture','{"reasoning_effort":"high"}','["6fb5d09327338227e757fc0a9819490bfb657187e365f198b33f3fb7a900e29c","cfc246d150bee593eb17355d080bde0215c46ad70fd8dfcae101695fc60f580f","d37da6a74fdcf3a527380a0fb395e0d8e287702aed030c4d082e8805378fdda8","d9295850fe76ac14051f62ddd64da79c34928adcb93df57d7be16d08524bac1c","fefc80a61783ee67626677d0474684b679e868c345c215775490dc270d74a6aa"]');
CREATE TABLE models(id TEXT PRIMARY KEY, backend_id TEXT NOT NULL REFERENCES backends(id),
                    native_id TEXT NOT NULL, revision TEXT NOT NULL,
                    UNIQUE(backend_id,native_id,revision));
INSERT INTO "models" VALUES('fixture','synthetic-local-v1','fixture-model','1');
CREATE TABLE providers(id TEXT PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO "providers" VALUES('synthetic','Synthetic fixture provider');
CREATE TABLE research(id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id),
                    property TEXT NOT NULL, value_json TEXT NOT NULL, verification TEXT NOT NULL, sources_json TEXT NOT NULL);
INSERT INTO "research" VALUES('6fb5d09327338227e757fc0a9819490bfb657187e365f198b33f3fb7a900e29c','fixture','context_window','4096','synthetic','[{"checked_at":"2026-11-14T00:00:00Z","title":"Included synthetic fixture specification","url":"fixture://gcb/offline-v1"}]');
INSERT INTO "research" VALUES('cfc246d150bee593eb17355d080bde0215c46ad70fd8dfcae101695fc60f580f','fixture','max_output','512','synthetic','[{"checked_at":"2026-11-14T00:00:00Z","title":"Included synthetic fixture specification","url":"fixture://gcb/offline-v1"}]');
INSERT INTO "research" VALUES('fefc80a61783ee67626677d0474684b679e868c345c215775490dc270d74a6aa','fixture','modalities','["text"]','synthetic','[{"checked_at":"2026-11-14T00:00:00Z","title":"Included synthetic fixture specification","url":"fixture://gcb/offline-v1"}]');
INSERT INTO "research" VALUES('d37da6a74fdcf3a527380a0fb395e0d8e287702aed030c4d082e8805378fdda8','fixture','native_tools','false','synthetic','[{"checked_at":"2026-11-14T00:00:00Z","title":"Included synthetic fixture specification","url":"fixture://gcb/offline-v1"}]');
INSERT INTO "research" VALUES('d9295850fe76ac14051f62ddd64da79c34928adcb93df57d7be16d08524bac1c','fixture','reasoning','{"native_configurations":[{"reasoning_effort":"low"},{"reasoning_effort":"high"}],"semantics":"effort"}','synthetic','[{"checked_at":"2026-11-14T00:00:00Z","title":"Included synthetic fixture specification","url":"fixture://gcb/offline-v1"}]');
CREATE INDEX research_model ON research(model_id,property);
COMMIT;
PRAGMA application_id=1195590193;
PRAGMA user_version=1;
