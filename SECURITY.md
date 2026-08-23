# Security Policy

## Supported versions

audio-leveler is pre-1.0. Security fixes land on the latest released `0.x` and `main`; older
builds are not separately patched — please update to the latest release.

| Version        | Supported          |
|----------------|--------------------|
| latest `0.1.x` | ✅                  |
| older          | ❌ (please update) |

## Reporting a vulnerability

Please report security issues **privately** — do **not** open a public issue.

- Preferred: this repository's **Security** tab → **Report a vulnerability** (a private GitHub
  security advisory).
- We aim to acknowledge within a few days. Coordinated disclosure is appreciated, and we're happy
  to credit you unless you'd prefer otherwise.

## Security model — please read before reporting

audio-leveler is a skill: installing it gives your agent scripts that run **on your machine, at
your own OS privilege, without a sandbox**. Some behaviour below is inherent to that and is **not**
a vulnerability.

- **It shells out to tools you installed.** `ffmpeg` and `ffprobe` do the measuring and rendering;
  `yt-dlp` fetches the source when you give it a URL. How ffmpeg parses a hostile media file is
  upstream's problem, not ours.
- **It fetches what you point it at.** Any URL goes to yt-dlp; Apple Podcasts links are resolved
  through `itunes.apple.com`. There is no allowlist, and none is intended.
- **It writes audio files.** `apply` renders to `<name>-leveled.mp3` — beside the source for a
  local file, in the current working directory for a URL — unless you pass `--out`. It refuses to
  clobber an existing output unless you pass `--force`, and never modifies the source in place.
  Downloaded sources are cached under `~/.cache/audio-leveler/` (`$XDG_CACHE_HOME` is respected).
- **Local trust boundary.** Cached downloads and rendered output sit under your user account with
  default filesystem permissions. Nothing is encrypted, and nothing is sent anywhere beyond the
  source fetch above — there is no telemetry.

## What we DO treat as vulnerabilities

- **Command or argument injection** into the `ffmpeg`, `ffprobe`, or `yt-dlp` invocations, from a
  source path, URL, media title, or filter spec. Every call passes an argument list; none goes
  through a shell, and a report that breaks that is a real finding.
- **Writing outside the requested output path**, or overwriting an existing file without
  `--force`.
- **Path escape** — a media title or episode metadata that writes outside the cache directory.
- **Anything leaving the machine** beyond fetching the source you asked for.

Thanks for helping keep audio-leveler users safe.
