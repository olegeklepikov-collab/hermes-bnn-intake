"""An additive extension of the host's existing SQLite database, not another DB."""
import hashlib
import json
import time


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def install(store):
    """Explicit operator migration. No migration in status/off/model hooks."""
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT value FROM meta WHERE key='semantic_intake_contract'").fetchone()
        if old:
            if old[0] not in {"1", "2", "3", "4", "5", "6"}:
                raise RuntimeError("intake_schema_not_supported")
            if old[0] == "6":
                verify(db)
                return {"schema_version": 6, "changed": False}
        else:
            db.execute("CREATE TABLE intake_modes(profile_id TEXT NOT NULL,sid TEXT NOT NULL,revision INTEGER NOT NULL,mode TEXT NOT NULL,actor TEXT NOT NULL,PRIMARY KEY(profile_id,sid))")
            db.execute("CREATE TABLE intake_revisions(profile_id TEXT NOT NULL,work_id TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(profile_id,work_id,revision),FOREIGN KEY(profile_id,work_id) REFERENCES working_cards(profile_id,work_id))")
            db.execute("CREATE TABLE intake_operations(profile_id TEXT NOT NULL,work_id TEXT NOT NULL,operation_id TEXT NOT NULL,input_hash TEXT NOT NULL,result TEXT NOT NULL,PRIMARY KEY(profile_id,work_id,operation_id))")
        if old is None or old[0] == "1":
            db.execute("CREATE TABLE intake_publications(profile_id TEXT NOT NULL,sid TEXT NOT NULL,turn_id TEXT NOT NULL,token TEXT NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(profile_id,sid,token))")
        if old is None or old[0] in {"1", "2"}:
            db.execute("CREATE TABLE intake_document_links(profile_id TEXT NOT NULL,work_id TEXT NOT NULL,sid TEXT NOT NULL,token TEXT NOT NULL,reference TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(profile_id,work_id,token))")
        if old is None or old[0] in {"1", "2", "3"}:
            db.execute("CREATE TABLE intake_model_outcomes(profile_id TEXT NOT NULL,id TEXT NOT NULL,work_id TEXT NOT NULL,sid TEXT NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(profile_id,id))")
        if old is None or old[0] in {"1", "2", "3", "4"}:
            db.execute("CREATE TABLE intake_path_attempts(profile_id TEXT NOT NULL,nonce TEXT NOT NULL,revision INTEGER NOT NULL,work_id TEXT NOT NULL,path_id TEXT NOT NULL,sid TEXT NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(profile_id,nonce,revision),FOREIGN KEY(profile_id,nonce) REFERENCES action_attempts(profile_id,nonce))")
            db.execute("CREATE INDEX intake_path_lookup ON intake_path_attempts(profile_id,work_id,path_id,nonce,revision)")
        db.execute("CREATE TABLE intake_delegations(profile_id TEXT NOT NULL,assignment_id TEXT NOT NULL,work_id TEXT NOT NULL,parent_sid TEXT NOT NULL,child_sid TEXT NOT NULL,body TEXT NOT NULL,sha256 TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(profile_id,assignment_id),FOREIGN KEY(profile_id,assignment_id) REFERENCES launch_grants(profile_id,id))")
        db.execute("INSERT INTO meta VALUES('semantic_intake_contract','6') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        verify(db)
    return {"schema_version": 6, "changed": True}


def verify(db, tables=None):
    marker = db.execute("SELECT value FROM meta WHERE key='semantic_intake_contract'").fetchone()
    if not marker or marker[0] != "6":
        raise RuntimeError("intake_explicit_migration_required")
    expected = {"intake_modes", "intake_revisions", "intake_operations", "intake_publications", "intake_document_links", "intake_model_outcomes", "intake_path_attempts", "intake_delegations"} if tables is None else tables
    actual = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not expected <= actual:
        raise RuntimeError("intake_schema_incomplete")


def read(db, profile, work, revision=None):
    if revision is None:
        row = db.execute("SELECT * FROM intake_revisions WHERE profile_id=? AND work_id=? ORDER BY revision DESC LIMIT 1", (profile, work)).fetchone()
    else:
        row = db.execute("SELECT * FROM intake_revisions WHERE profile_id=? AND work_id=? AND revision=?", (profile, work, revision)).fetchone()
    if not row:
        return None
    body = json.loads(row["body"])
    if digest(body) != row["sha256"]:
        raise PermissionError("intake_revision_integrity_failed")
    return body


def append(db, profile, work, body):
    db.execute("INSERT INTO intake_revisions VALUES(?,?,?,?,?,?)",
               (profile, work, body["revision"], canonical(body), digest(body), time.time()))
