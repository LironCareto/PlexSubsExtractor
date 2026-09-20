# PlexSubsExtractor

Extract subtitle files that Plex Media Server has stored inside its internal subtitle blob database and save them as normal sidecar files next to the corresponding movie or episode.

The main goal is simple: turn subtitles downloaded through Plex into ordinary files such as:

```text
Movies/
└── Alien (1979)/
    ├── Alien (1979).mkv
    └── Alien (1979).eng.srt
```

## Safety first

PlexSubsExtractor is deliberately conservative:

- Plex SQLite databases are opened with `mode=ro`.
- `PRAGMA query_only = ON` adds a second read-only safeguard.
- The default mode is **dry-run**.
- No subtitle file is created unless you explicitly pass `--write`.
- Existing subtitle files are never overwritten unless you explicitly pass `--force`.
- The script contains no SQL writes or commits to the Plex databases.

In other words, the script reads Plex's databases and writes subtitle sidecar files. It does not modify Plex's databases.

## Requirements

- Python 3.10+
- No third-party Python packages

## Usage

### 1. Dry-run first

```bash
python3 plex_subs_extractor.py \
  -d "/path/to/Plex Media Server/Plug-in Support/Databases"
```

This only shows what would be extracted.

Example:

```text
[FOUND] /media/Movies/Alien (1979)/Alien (1979).mkv
        language=eng codec=srt forced=no
     -> /media/Movies/Alien (1979)/Alien (1979).eng.srt
        [DRY RUN: not written]
```

### 2. Write the subtitle files

Once the dry-run looks correct:

```bash
python3 plex_subs_extractor.py \
  -d "/path/to/Plex Media Server/Plug-in Support/Databases" \
  --write
```

### Path mapping

If Plex stores a media path as:

```text
/movies/Alien (1979)/Alien (1979).mkv
```

but the machine running the extractor sees it as:

```text
/srv/media/Movies/Alien (1979)/Alien (1979).mkv
```

use:

```bash
python3 plex_subs_extractor.py \
  -d "/path/to/Databases" \
  --path-map "/movies=/srv/media/Movies"
```

`--path-map` can be supplied more than once.

### Filter by language

```bash
python3 plex_subs_extractor.py \
  -d "/path/to/Databases" \
  --language eng
```

The comparison is made against the language value stored by Plex for the subtitle stream.

### Overwrite existing subtitles

Existing sidecar files are skipped by default.

To overwrite them explicitly:

```bash
python3 plex_subs_extractor.py \
  -d "/path/to/Databases" \
  --write \
  --force
```

## How it works

Plex stores on-demand/uploaded subtitle payloads in:

```text
com.plexapp.plugins.library.blobs.db
```

Subtitle blobs use `blob_type = 3`. Their `linked_id` maps to a row in `media_streams` in:

```text
com.plexapp.plugins.library.db
```

The extractor follows that relationship to `media_parts.file`, decompresses the gzip subtitle blob, and creates a sidecar file next to the corresponding video.

Forced subtitles are named in Plex-compatible form:

```text
Movie.eng.forced.srt
```

## Should Plex be stopped first?

The script cannot write to Plex's databases, even if Plex is running.

However, Plex itself may be updating its two databases while they are being read. For the most consistent possible snapshot, stopping Plex briefly before a large extraction is still a sensible precaution.

## What this does not do

- It does not extract subtitle tracks embedded inside MKV/MP4 files.
- It does not modify Plex metadata.
- It does not update Plex's databases.
- It does not call OpenSubtitles or any other external subtitle service.
- It cannot guarantee compatibility with future Plex database schema changes; Plex's internal database structure is not a public stable API.

## Acknowledgements

The database relationship used here is also used by [danrahn/PlexSubtitleExtractor](https://github.com/danrahn/PlexSubtitleExtractor), which was useful as a reference when verifying Plex's subtitle blob layout.

This implementation focuses on a stricter safety model: hard SQLite read-only access, dry-run by default, explicit writes, and optional path remapping.

## License

MIT. See [LICENSE](LICENSE).

---

Plex is a trademark of Plex, Inc. This project is not affiliated with or endorsed by Plex.
