#!/usr/bin/env python3
"""Extract subtitle blobs downloaded by Plex into sidecar subtitle files.

The Plex databases are always opened in SQLite read-only mode. The script is a
no-op by default and only writes subtitle files when --write is explicitly used.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

LIBRARY_DB = "com.plexapp.plugins.library.db"
BLOBS_DB = "com.plexapp.plugins.library.blobs.db"
DEFAULT_CONFIG = Path("config.json")


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database with two independent read-only safeguards."""
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def parse_path_map(value: str) -> tuple[str, str]:
    """Parse FROM=TO used to translate Plex-visible paths to host paths."""
    if "=" not in value:
        raise argparse.ArgumentTypeError("path mapping must have the form FROM=TO")

    source, target = value.split("=", 1)
    source = source.rstrip("/\\")
    target = target.rstrip("/\\")

    if not source or not target:
        raise argparse.ArgumentTypeError("path mapping must have the form FROM=TO")

    return source, target


def load_config(config_path: Path) -> dict:
    """Load optional local configuration from JSON."""
    if not config_path.is_file():
        return {}

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read config file {config_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Config file {config_path} must contain a JSON object")

    return data


def config_path_maps(config: dict) -> list[tuple[str, str]]:
    """Read path mappings from config.json."""
    raw = config.get("path_maps", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("'path_maps' in config must be a JSON array")

    mappings: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("Each 'path_maps' entry must be a string in FROM=TO form")
        try:
            mappings.append(parse_path_map(item))
        except argparse.ArgumentTypeError as exc:
            raise ValueError(f"Invalid path mapping in config: {item!r}") from exc

    return mappings


def apply_path_maps(path: str, mappings: list[tuple[str, str]]) -> Path:
    """Apply the first matching Plex-path to local-path mapping."""
    for source, target in mappings:
        if path == source:
            return Path(target)

        for separator in ("/", "\\"):
            prefix = source + separator
            if path.startswith(prefix):
                remainder = path[len(source) :].lstrip("/\\")
                return Path(target) / Path(remainder)

    return Path(path)


def normalize_extension(codec: str | None) -> str:
    """Translate common Plex codec names to conventional file extensions."""
    if not codec:
        return "srt"

    codec = codec.lower().lstrip(".")
    return {
        "subrip": "srt",
        "webvtt": "vtt",
    }.get(codec, codec)


def subtitle_filename(
    video_path: Path,
    language: str | None,
    forced: bool,
    codec: str | None,
) -> Path:
    """Build a Plex-compatible sidecar subtitle filename."""
    stem = video_path.with_suffix("")
    language_tag = language or "und"
    forced_tag = ".forced" if forced else ""
    extension = normalize_extension(codec)
    return Path(f"{stem}.{language_tag}{forced_tag}.{extension}")


def load_blobs(blob_db: Path) -> tuple[dict[int, bytes], int]:
    """Load and decompress Plex subtitle blobs (blob_type=3)."""
    subtitles: dict[int, bytes] = {}
    failed = 0

    with open_readonly(blob_db) as conn:
        rows = conn.execute(
            "SELECT linked_id, blob FROM blobs WHERE blob_type = 3"
        )
        for row in rows:
            try:
                subtitles[int(row["linked_id"])] = gzip.decompress(row["blob"])
            except Exception as exc:
                failed += 1
                print(
                    f"[ERROR] Could not decompress subtitle stream "
                    f"{row['linked_id']}: {exc}",
                    file=sys.stderr,
                )

    return subtitles, failed


def get_stream_info(conn: sqlite3.Connection, stream_id: int) -> sqlite3.Row | None:
    """Resolve a subtitle stream to its media file and subtitle metadata."""
    return conn.execute(
        """
        SELECT
            parts.file,
            stream.codec,
            stream.language,
            stream.forced
        FROM media_streams AS stream
        INNER JOIN media_parts AS parts
            ON parts.id = stream.media_part_id
        WHERE stream.id = ?
        """,
        (stream_id,),
    ).fetchone()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract subtitle blobs stored by Plex into sidecar files without "
            "modifying the Plex databases. Dry-run is the default."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=(
            "Local JSON configuration file. Defaults to ./config.json if present. "
            "The file can contain database_folder and path_maps."
        ),
    )
    parser.add_argument(
        "-d",
        "--database-folder",
        type=Path,
        help=(
            "Plex 'Plug-in Support/Databases' directory. Overrides database_folder "
            "from the config file."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually create subtitle files. Without this flag nothing is written.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing subtitle files (only meaningful with --write).",
    )
    parser.add_argument(
        "--language",
        help="Only process this Plex language tag, e.g. spa, eng, es or en.",
    )
    parser.add_argument(
        "--path-map",
        action="append",
        default=[],
        type=parse_path_map,
        metavar="FROM=TO",
        help=(
            "Map a path stored by Plex to a path visible to this machine. "
            "May be repeated. If supplied, command-line mappings replace config mappings."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        config = load_config(args.config)
        configured_database = config.get("database_folder")

        if args.database_folder is not None:
            database_folder = args.database_folder
        elif configured_database:
            if not isinstance(configured_database, str):
                raise ValueError("'database_folder' in config must be a string")
            database_folder = Path(configured_database)
        else:
            raise ValueError(
                "No database folder configured. Set database_folder in config.json "
                "or pass --database-folder."
            )

        path_maps = args.path_map if args.path_map else config_path_maps(config)
    except ValueError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    library_db = database_folder / LIBRARY_DB
    blob_db = database_folder / BLOBS_DB

    print("PlexSubsExtractor")
    print("=================")
    print(f"Library DB : {library_db}")
    print(f"Blobs DB   : {blob_db}")
    print("DB access  : READ ONLY (SQLite mode=ro + PRAGMA query_only)")
    print(f"Output     : {'WRITE' if args.write else 'DRY RUN'}")
    print()

    try:
        blobs, decompress_errors = load_blobs(blob_db)
        conn = open_readonly(library_db)
    except (OSError, sqlite3.Error) as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        return 2

    matched = 0
    written = 0
    existing = 0
    missing_media = 0
    errors = decompress_errors

    try:
        for stream_id, subtitle_data in blobs.items():
            try:
                row = get_stream_info(conn, stream_id)
            except sqlite3.Error as exc:
                print(f"[ERROR] stream {stream_id}: {exc}", file=sys.stderr)
                errors += 1
                continue

            if row is None:
                print(f"[WARN] stream {stream_id}: no associated media file")
                missing_media += 1
                continue

            language = row["language"]
            if args.language and (language or "").casefold() != args.language.casefold():
                continue

            video_path = apply_path_maps(row["file"], path_maps)
            target = subtitle_filename(
                video_path=video_path,
                language=language,
                forced=bool(row["forced"]),
                codec=row["codec"],
            )
            matched += 1

            print(f"[FOUND] {video_path}")
            print(
                f"        language={language or 'und'} "
                f"codec={row['codec'] or 'unknown'} "
                f"forced={'yes' if row['forced'] else 'no'}"
            )
            print(f"     -> {target}")

            if target.exists() and not args.force:
                print("        [SKIP: target already exists]")
                existing += 1
                print()
                continue

            if not args.write:
                print("        [DRY RUN: not written]")
                print()
                continue

            if not target.parent.is_dir():
                print(f"        [ERROR: directory does not exist: {target.parent}]")
                errors += 1
                print()
                continue

            try:
                target.write_bytes(subtitle_data)
                written += 1
                print("        [WRITTEN]")
            except OSError as exc:
                errors += 1
                print(f"        [ERROR: {exc}]")

            print()
    finally:
        conn.close()

    print("Summary")
    print("=======")
    print(f"Subtitle blobs   : {len(blobs)}")
    print(f"Matched          : {matched}")
    print(f"Written          : {written}")
    print(f"Already existing : {existing}")
    print(f"Missing media    : {missing_media}")
    print(f"Errors           : {errors}")

    if not args.write:
        print("\nDRY RUN ONLY. Nothing was written. Add --write to create sidecar files.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
