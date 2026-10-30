"""Read/write the project-local WorkDrive manifest (metadata only)."""
import json
import os
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_DIR / "state/manifest.json"
LEGACY_DOCS_MANIFEST = PROJECT_DIR / "state/docs-manifest.json"


def source_key_for(source):
    source = Path(source).resolve()
    try:
        return source.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(source)


def read_manifest(source_key):
    """Return (document, source_files_or_None), migrating legacy data in memory only."""
    path = MANIFEST if MANIFEST.is_file() else None
    if path is None and LEGACY_DOCS_MANIFEST.is_file():
        path = LEGACY_DOCS_MANIFEST

    if path is None:
        return {"version": 2, "sources": {}}, None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read manifest: {exc}") from exc

    if "sources" not in data:
        # Migrate the existing Docs-only manifest in memory; write the new schema
        # only after a successful --apply run.
        data = {
            "version": 2,
            "sources": {
                "NK/Docs": {
                    "remote_folder_id": None,
                    "files": data.get("files", {}),
                }
            },
        }
    else:
        data.setdefault("version", 2)
        data.setdefault("sources", {})

    entry = data["sources"].get(source_key)
    return data, entry.get("files", {}) if entry else None


def write_manifest(data, source_key, remote_folder_id, local_files, remote_files, preserve_paths=()):
    """Atomically write source metadata and retain old entries for skipped files."""
    sources = data.setdefault("sources", {})
    existing = sources.get(source_key, {})
    files = {
        rel: {
            "size": meta["size"],
            "mtime_ns": meta["mtime_ns"],
            "remote_id": remote_files[rel]["id"],
            "remote_size": remote_files[rel]["size"],
            **({"download_verified": True} if remote_files[rel].get("_download_verified") else {}),
        }
        for rel, meta in local_files.items()
        if rel in remote_files and remote_files[rel]["size"] == meta["size"]
    }
    for rel in preserve_paths:
        if rel in existing.get("files", {}):
            files[rel] = existing["files"][rel]
    sources[source_key] = {
        "remote_folder_id": remote_folder_id,
        "files": files,
    }
    data["version"] = 2
    MANIFEST.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(MANIFEST.parent, 0o700)
    fd, temp_name = tempfile.mkstemp(prefix="manifest.", suffix=".tmp", dir=MANIFEST.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, MANIFEST)
        os.chmod(MANIFEST, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
