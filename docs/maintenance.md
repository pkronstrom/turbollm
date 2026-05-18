# `turbo prune` — housekeeping for recordings

`turbo prune` removes old audio recordings and their associated Obsidian
markdown notes and keyframe attachments. It operates on the same directory
layout that `record-to-obsidian` and `record-meeting-with-screen` produce.

## Synopsis

```
turbo prune (--older-than DURATION | --keep-last N)
            [--vault PATH]
            [--audio-dir PATH]
            [--apply]
```

`--older-than` and `--keep-last` are **mutually exclusive**; exactly one
must be provided.

Without `--apply` the command prints a dry-run table and exits. Nothing
is deleted until you add `--apply`.

## Flags

| Flag | Default | Description |
|---|---|---|
| `--older-than DURATION` | — | Prune recordings whose audio mtime is older than DURATION. Accepts `Nd` (days), `Nw` (weeks), `Nm` (months ≈ 30 d), `Ny` (years ≈ 365 d). |
| `--keep-last N` | — | Keep the N recordings with the newest audio mtime; prune the rest. |
| `--vault PATH` | `$OBSIDIAN_VAULT` or `~/Documents/Obsidian` | Obsidian vault root. Markdown and attachments are looked up under `<vault>/Meetings/`. |
| `--audio-dir PATH` | `$TURBO_AUDIO_INBOX` or `~/Recordings/turbo` | Directory that holds `<slug>.wav` files. |
| `--apply` | off (dry-run) | Delete the listed files. Without this flag, only the table is printed. |

## What gets deleted

For each recording slug, `turbo prune` identifies up to four artifacts:

```
<audio-dir>/<slug>.wav
<vault>/Meetings/<slug>.md
<vault>/Meetings/<slug>.raw.md
<vault>/Meetings/attachments/<slug>/   ← directory, deleted recursively
```

**Slug derivation:** the slug is the audio filename without the `.wav`
extension. Turbo's default naming is `YYYY-MM-DD-HHmmss`, e.g.
`2026-05-01-143000`.

**Orphan detection:** if `<vault>/Meetings/<slug>.md` exists but the
matching `.wav` has been moved or deleted, the markdown and attachments
are still eligible for pruning (the markdown's own mtime is used instead
of the audio mtime).

## Dry-run output format

Each eligible slug produces one tab-delimited line:

```
<slug>  <total-bytes>  <file-count>
```

After all slug lines, a separator `---` and a totals line:

```
Total: <bytes> bytes, <N> recording(s)
```

Example with three slugs:

```
2026-04-10-090000  14523904  4
2026-04-12-143000  9437184   3
2026-04-15-083000  18874368  4
---
Total: 42835456 bytes, 3 recording(s)
```

The slug lines start with a `YYYY-` prefix, so `grep -cE '^[0-9]{4}-'`
gives the count of candidates — useful in scripts.

## Examples

### List recordings older than 30 days (dry-run)

```bash
turbo prune --older-than 30d
```

No files are touched. Review the table, then add `--apply` to delete.

### Delete recordings older than 30 days

```bash
turbo prune --older-than 30d --apply
```

Output on success:

```
Deleted 3 slug(s), freed 40.8 MB
```

### Keep only the 50 most recent recordings (dry-run)

```bash
turbo prune --keep-last 50
```

Lists every recording except the 50 newest. Add `--apply` to remove them.

### Keep only the 50 most recent recordings (destructive)

```bash
turbo prune --keep-last 50 --apply
```

### Use a custom vault or audio directory

```bash
turbo prune --older-than 7d \
            --vault /Volumes/NAS/Obsidian \
            --audio-dir /Volumes/NAS/Recordings
```

### Parse the dry-run output in a shell script

```bash
# Count how many recordings would be pruned.
turbo prune --older-than 14d | grep -cE '^[0-9]{4}-'

# Extract slug names only.
turbo prune --older-than 14d | awk '/^[0-9]{4}-/{print $1}'
```

## Error cases

| Situation | Exit code | Message |
|---|---|---|
| Both `--older-than` and `--keep-last` given | non-zero | `--older-than and --keep-last are mutually exclusive` |
| Neither flag given | non-zero | `must specify one of --older-than or --keep-last` |
| Invalid duration suffix (e.g. `5x`) | non-zero | `invalid duration '5x'` |
| No recordings match criteria | 0 | *(no output; silent success)* |

## Duration reference

| Suffix | Meaning |
|---|---|
| `d` | days (exact) |
| `w` | weeks (7 d each) |
| `m` | months (≈ 30 d each) |
| `y` | years (≈ 365 d each) |

Examples: `7d` = 7 days, `2w` = 14 days, `3m` = ~90 days, `1y` = ~365 days.
