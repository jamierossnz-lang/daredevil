# Daredevil

A self-hosted front end for an *arr media stack. [Sonarr](https://sonarr.tv/) (TV) and [Radarr](https://radarr.video/) (movies) store the library, monitor for new releases, grab them through [qBittorrent](https://www.qbittorrent.org/) and import finished downloads into a Plex-ready folder structure; [Prowlarr](https://prowlarr.com/) manages the indexers for both. Daredevil is the one place you search, add, monitor, download, retry and keep an eye on all of it — plus discovery (For You, streaming charts, [TMDB](https://www.themoviedb.org/)) and Plex clean-up.

---

## Features

- **TV shows (Sonarr)** — search and add shows, turn monitoring on/off per show, season and episode, download a whole show, seasons or single episodes, pick a release yourself, retry or reset — all stored in and carried out by Sonarr. Library tabs: All, Recently added, From For You, and Watching (what's been played in Plex lately)
- **Movies (Radarr)** — the same for movies; Radarr's minimum availability watches unreleased movies and grabs them once they're out
- **One Add dialog everywhere** — *Add to Library* (saved, nothing downloads), *Add & pick release*, or *Add to Plex* (monitored and searched). New shows default to future episodes if they're still airing and to nothing if they've ended. You stay on the page; a banner links to what you added
- **Search (Prowlarr)** — TV or Movies, with Prowlarr's `{Token:value}` parameters, indexer and category filters. *Download* hands a result to Sonarr or Radarr (adding the show/movie first if it isn't in the library yet)
- **Download queue** — Sonarr's and Radarr's queues in one place with qBittorrent's live speed/peers: pause, retry with a different release, remove; waiting-for-release (TV and Movies), wanted, history, failed
- **Torrent Manager** — every torrent in qBittorrent: filter by state, category or name, start, pause, remove (with or without files), one at a time or in bulk
- **Imports** — Sonarr/Radarr move and rename finished downloads; Daredevil confirms each file really landed in the library
- **Settings** — Prowlarr, Sonarr, Radarr, qBittorrent and Background Tasks tabs: connections, indexers (add with category/language filters, test, edit), download and library folders with folder pickers, quality profiles and sizes, naming, add defaults, Plex (refresh on import, Watchlist), and one-click wiring (qBittorrent, webhooks, indexer sync)
- **File browser** — the download and library folders appear automatically; hand files to Sonarr/Radarr to import
- **For You** — swipe through recommendations based on your library plus TMDB's trending, new, coming-soon and popular titles

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.11+ | Not needed if using Docker |
| Redis | 7+ | Required for background tasks (Celery) — included in Docker |
| qBittorrent | 4.6+ | Web UI must be enabled — included in Docker |
| Sonarr / Radarr / Prowlarr | v4 / v5+ / v1+ | Included in Docker; their data lives in the same PostgreSQL as Daredevil's |

---

## Installation

### Docker (recommended)

Docker Compose runs the full stack — Daredevil, Sonarr, Radarr, Prowlarr, qBittorrent, Plex, Redis, and Celery — in one command. Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```
               ┌──────────────┐  indexers   ┌──────────┐
               │   Prowlarr   │────────────▶│  Sonarr  │──┐ grabs   ┌──────────────┐
               │   :9696      │────────┐    │  :8989   │  ├────────▶│ qBittorrent  │
               └──────▲───────┘        └───▶│  Radarr  │──┘         │  :8080       │
                      │ search              │  :7878   │◀── imports └──────────────┘
┌─────────────┐       │                     └────▲─────┘   finished       │
│  Daredevil  │───────┴──────────────────────────┘ API + webhooks         │ /downloads
│  :8000      │                                                           ▼
└─────────────┘                      movies + tv folders ───────────▶ Plex :32400
```

**Quick start:**

```bash
# 1. Clone and configure
git clone https://github.com/jamierossnz-lang/daredevil.git
cd daredevil
cp .env.example .env
```

Edit `.env` and set at minimum:
- `TMDB_API_KEY` — get free at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api)
- `PLEX_CLAIM` — get a 4-minute token at [plex.tv/claim](https://www.plex.tv/claim/) (only needed for first run)
- `TZ` — your timezone, e.g. `Pacific/Auckland`

```bash
# 2. Start everything
docker compose up -d

# 3. Get the qBittorrent temporary password (generated on first run)
docker compose logs qbittorrent | grep "temporary password"

# 4. Create a Daredevil admin user — this is also your login for the app itself
docker compose exec web python manage.py createsuperuser
```

Daredevil requires signing in — there's no separate account system, the superuser
you just created is the login for the whole app (not just `/admin/`). There's no
signup page; it's a single-user home server.

| Service | URL |
|---------|-----|
| Daredevil | http://localhost:8000 |
| Sonarr | http://localhost:8989 |
| Radarr | http://localhost:7878 |
| Prowlarr | http://localhost:9696 |
| qBittorrent | http://localhost:8081 |
| Plex | http://localhost:32400/web |

---

**Using Docker Desktop GUI (instead of the terminal):**

After `docker compose up -d`, open Docker Desktop. The entire stack appears as a group called **daredevil** under the **Containers** tab.

| Task | How |
|------|-----|
| Watch logs | Click a container (e.g. `web`) → **Logs** tab |
| Get the qBittorrent temp password | Click `qbittorrent` → **Logs** tab, look for `temporary password is provided` |
| Open a shell (e.g. to create a superuser) | Click `web` → **Exec** tab → type `python manage.py createsuperuser` |
| Restart a container | Click the restart icon next to the container name |
| Stop everything | Click **Stop** on the `daredevil` group row |
| Browse volumes | **Volumes** tab in the left sidebar |

> To create the admin user via the Docker Desktop GUI: open the `web` container → **Exec** tab → run `python manage.py createsuperuser` and follow the prompts. You can then log in at http://localhost:8000 — the same account signs into the app itself and `/admin/`.

---

**Set up qBittorrent after first run:**

1. Log in at http://localhost:8080 with username `admin` and the temporary password from the logs
2. Go to Tools → Options → Web UI and set a permanent password
3. Update `QBITTORRENT_PASSWORD` in `.env` to match, then `docker compose restart web worker`

**Set up Sonarr, Radarr and Prowlarr:**

Daredevil drives these three apps — your shows and movies are stored in Sonarr and Radarr, and Prowlarr feeds both with indexers.

1. Open Sonarr (:8989), Radarr (:7878) and Prowlarr (:9696) once each and finish their first-run login setup.
2. In each app, copy the **API key** from *Settings → General*.
3. In Daredevil → **App Settings**, paste each key into the **Sonarr**, **Radarr** and **Prowlarr** tabs and **Save**. The URLs default to the Docker service names (`http://sonarr:8989`, `http://radarr:7878`, `http://prowlarr:9696`).
4. On the **Sonarr** and **Radarr** tabs:
   - **Connect** qBittorrent (uses the login from the **qBittorrent** tab),
   - **Connect** the webhook (so grabs and imports reach Daredevil instantly),
   - under **Folders**, add a library root folder — `/tv` for Sonarr, `/movies` for Radarr — and optionally a download folder for its qBittorrent category (the folder buttons browse each app's own view of the disk),
   - under **Plex**, switch on *Refresh Plex when files change* (and the Plex Watchlist if you want it).
5. On the **Prowlarr** tab: **Add indexer** (600+ to pick from — filter by TV/Movies and language), then **Connect Sonarr & Radarr** so they receive every indexer automatically.
6. Upgrading from an older Daredevil? The Sonarr/Radarr tabs show an **Import your old Daredevil library** button (or run `docker compose exec web python manage.py import_library_to_arr`). It adds your existing shows/movies without re-downloading anything — files already on disk are picked up.

> **Hardlinks:** qBittorrent keeps seeding after an import, so Sonarr/Radarr either hardlink (no extra space) or copy the file. Hardlinks only work when the download and library folders are on the same filesystem *and* the same mount inside the container; with the separate `DOWNLOADS_DIR` / `TV_DIR` / `MOVIES_DIR` mounts they fall back to copying.

**Set up Plex libraries:**

In Plex → Libraries → Add Library, point it at:
- Movies → `/data/movies`
- TV Shows → `/data/tv`

---

**Useful commands:**

```bash
docker compose logs -f web          # Daredevil logs
docker compose logs -f qbittorrent  # qBittorrent logs
docker compose logs -f sonarr       # Sonarr logs (also radarr, prowlarr)
docker compose logs -f plex         # Plex logs
docker compose restart web          # restart after .env change
docker compose pull                 # update all third-party images
docker compose build                # rebuild Daredevil after code change
docker compose up -d                # apply changes
docker compose down                 # stop all containers
docker compose down -v              # ⚠ stop + delete all data volumes
```

**Updating Daredevil:**

```bash
git pull
docker compose build web worker beat
docker compose up -d
```

> **First update to the Sonarr/Radarr version:** `docker compose up -d` also creates the new `prowlarr`, `sonarr` and `radarr` containers. Jackett is no longer part of the stack — add `--remove-orphans` to remove its old container (its `data/jackett` folder is left alone). Then follow *Set up Sonarr, Radarr and Prowlarr* above.

**Using existing folders on the host (instead of Docker volumes):**

By default, media is stored in Docker-managed named volumes. To use folders already on your machine, replace the volume references in `docker-compose.yml`:

```yaml
# macOS / Linux
volumes:
  - /Volumes/nas/downloads:/media/downloads
  - /Volumes/nas/movies:/media/movies
  - /Volumes/nas/tv:/media/tv
```

```yaml
# Windows (Docker Desktop) — forward slashes
volumes:
  - D:/Downloads:/media/downloads
  - D:/Movies:/media/movies
  - D:/TV:/media/tv
```

Apply the same mounts to the `web`, `worker`, and `qbittorrent` services. qBittorrent's mount must use `/downloads` as the container path.

---

### Unraid

**Before you start:** Install the **Docker Compose Manager** plugin by dcflachs from Community Applications. This adds `docker-compose` to Unraid — without it, compose commands won't work regardless of Unraid version.

**1. Open a terminal**

Unraid WebUI → Tools → Terminal (or SSH in).

**2. Clone the repo into your appdata share**

```bash
cd /mnt/user/appdata
git clone https://github.com/jamierossnz-lang/daredevil.git
cd daredevil
cp .env.example .env
```

**3. Edit `.env`**

```bash
nano .env
```

Key values to set:

```env
# Required
TMDB_API_KEY=your_tmdb_api_key
SECRET_KEY=change-me-to-a-long-random-string
TZ=Pacific/Auckland          # your timezone

# Unraid paths — adjust share names to match yours
DATA_DIR=/mnt/user/appdata/daredevil
DOWNLOADS_DIR=/mnt/user/data/torrents
MOVIES_DIR=/mnt/user/media/movies
TV_DIR=/mnt/user/media/tv

# Unraid default UID/GID (nobody/users)
PUID=99
PGID=100

# PostgreSQL password (change this)
DB_PASSWORD=changeme

# Plex — skip PLEX_CLAIM if Plex is already running as a CA app
PLEX_CLAIM=claim-xxxxxxxxxxxxxx
```

> **Tip:** Check your user's UID/GID with `id <username>` in the terminal.

**4. If qBittorrent or Plex is already installed via Community Applications**

Skip those services in the compose file — they'll conflict. Comment out the `qbittorrent` and/or `plex` blocks in `docker-compose.yml`, then point Daredevil at your existing containers instead:

```env
# In .env — point at your existing CA containers
QBITTORRENT_HOST=<your-qbt-container-ip-or-name>
QBITTORRENT_PORT=8080
PLEX_URL=http://<your-plex-container-ip>:32400
PLEX_TOKEN=<your-plex-token>
```

**5. Start the stack**

```bash
docker-compose up -d
```

> **Note:** Unraid uses `docker-compose` (with a hyphen), not `docker compose`. Use the hyphenated form for all commands below.

**6. Create an admin user**

```bash
docker-compose exec web python manage.py createsuperuser
```

**Service URLs** (replace `tower` with your Unraid hostname or IP):

| Service | URL |
|---------|-----|
| Daredevil | http://tower:8000 |
| Sonarr | http://tower:8989 |
| Radarr | http://tower:7878 |
| Prowlarr | http://tower:9696 |
| qBittorrent | http://tower:8081 (note: Unraid WebUI uses 8080) |
| Plex | http://tower:32400/web |
| PostgreSQL | internal only (not exposed) |

> **Port note:** Unraid's own WebUI runs on port 8080, so qBittorrent is mapped to **8081** in this stack to avoid the conflict.

**Updating:**

```bash
cd /mnt/user/appdata/daredevil
bash scripts/backup-db.sh
git pull
docker-compose build web worker beat
docker-compose up -d
```

> The first `up -d` after updating to the Sonarr/Radarr version creates the `db-init`, `prowlarr`, `sonarr` and `radarr` containers and restarts PostgreSQL once (it now allows 100 connections). `db-init` creates the apps' databases; their settings and libraries then live in PostgreSQL. Rebuilding only `web worker beat` won't start them.
> If Sonarr, Radarr or Prowlarr already run as Community Apps, comment their blocks out of `docker-compose.yml` and point Daredevil at them from **App Settings** instead (use the Unraid IP, e.g. `http://192.168.1.10:8989`), then set the **path mappings** on each tab to match your shares.

---

### macOS / Linux

```bash
# 1. Clone the repo
git clone https://github.com/jamierossnz-lang/daredevil.git
cd daredevil

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file
cp .env.example .env
# Edit .env with your settings (see Configuration below)

# 5. Run database migrations
python manage.py migrate

# 6. Create an admin user
python manage.py createsuperuser

# 7. Start the server (also starts Celery if Redis is running)
bash start.sh
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

---

### Windows

> Daredevil runs on Windows via WSL2 or natively with a few extra steps.

#### Option A — WSL2 (recommended)

1. Install [WSL2](https://learn.mic rosoft.com/en-us/windows/wsl/install) and a Linux distro (Ubuntu 22.04 works well)
2. Inside WSL, follow the **macOS / Linux** steps above
3. Redis: `sudo apt install redis-server && sudo service redis-server start`
4. Access Daredevil at `http://localhost:8000` from your Windows browser

To access Windows folders from WSL, they're mounted at `/mnt/c/`, `/mnt/d/`, etc.  
Example: `D:\Movies` → `/mnt/d/Movies`

#### Option B — Native Windows

```powershell
# 1. Install Python 3.11+ from python.org (tick "Add to PATH")
# 2. Clone the repo
git clone https://github.com/jamierossnz-lang/daredevil.git
cd daredevil

# 3. Create virtualenv
python -m venv venv
venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Create .env (copy and edit)
copy .env.example .env

# 6. Migrate and create superuser
python manage.py migrate
python manage.py createsuperuser

# 7. Start Django
python manage.py runserver
```

For Redis on Windows, use [Memurai](https://www.memurai.com/) (free for development) or run Redis inside WSL and connect to it from Windows (`redis://localhost:6379/0`).

To start Celery on Windows (two separate terminals):
```powershell
# Terminal 1 — worker
venv\Scripts\celery -A config worker -l info -P solo

# Terminal 2 — beat scheduler
venv\Scripts\celery -A config beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

> **Note:** Use `-P solo` on Windows — the default prefork pool doesn't work on Windows.

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```env
SECRET_KEY=change-me-to-a-long-random-string
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# TMDB — get a free API key at https://www.themoviedb.org/settings/api
TMDB_API_KEY=your_tmdb_api_key

# qBittorrent Web UI
QBITTORRENT_HOST=localhost        # hostname or IP of the machine running qBT
QBITTORRENT_PORT=8080
QBITTORRENT_USERNAME=admin
QBITTORRENT_PASSWORD=adminadmin

# Redis (for Celery background tasks)
REDIS_URL=redis://localhost:6379/0

# Prowlarr / Sonarr / Radarr — optional here: they're configured on the
# App Settings page (stored in the database). These only seed it on first start.
SONARR_URL=http://sonarr:8989
SONARR_API_KEY=
RADARR_URL=http://radarr:7878
RADARR_API_KEY=
PROWLARR_URL=http://prowlarr:9696
PROWLARR_API_KEY=

# How Sonarr/Radarr reach Daredevil for webhooks (Docker service name by default)
DAREDEVIL_INTERNAL_URL=http://web:8000
```

---

## qBittorrent setup

1. Open qBittorrent → **Tools → Options → Web UI** → enable Web UI, set a username/password
2. In Daredevil, go to **App Settings → qBittorrent** and enter the same login, then **Save & test**
3. Connect it to Sonarr, Radarr and Prowlarr from their tabs on **App Settings** — each adds torrents under its own category (`sonarr`, `radarr`, `prowlarr`)

> If qBittorrent runs on a different machine, set `QBITTORRENT_HOST` to that machine's hostname or IP.
> qBittorrent bans an IP after 5 failed logins (for an hour by default). If the queue shows *qBittorrent unreachable — banned*, fix the password on **App Settings → qBittorrent** and wait for the ban to expire (or restart qBittorrent).

---

## Folder structure for Plex

Sonarr and Radarr name and folder files themselves when they import (configurable on their **App Settings** tabs):

```
movies/
  Inception (2010)/
    Inception (2010) Bluray-1080p.mkv

tv/
  Breaking Bad/
    Season 1/
      Breaking Bad - S01E01 - Pilot Bluray-1080p.mkv
```

---

## Background tasks (Celery)

Searching, downloading, importing and watching for releases are done by Sonarr and Radarr themselves, and they report back through webhooks — Daredevil doesn't poll for any of it. Celery only runs Daredevil's own housekeeping:
- *Keep pages fast* (every 10 minutes) — refetches what the Dashboard, Streaming and Plex pages show from TMDB and Plex, so they open from the cache
- Storage alerts (every 6 hours)
- For You recommendations and new/trending titles from TMDB (nightly)
- The one-off *Import old library into Sonarr/Radarr* task

Schedules and recent runs are on **App Settings → Background Tasks**.

---

## Databases and backups

Daredevil, Sonarr, Radarr and Prowlarr all keep their data in the one PostgreSQL container (`db`). Each *arr app has a main and a log database (`sonarr-main`, `sonarr-log`, …), created by the one-shot `db-init` service; their API keys stay in `config.xml` in `DATA_DIR`.

`scripts/backup-db.sh` dumps Daredevil's database and the three apps' main databases to `DAREDEVIL_BACKUP_DIR` (default `/mnt/user/Backups/daredevil`) and keeps 14 days (`DAREDEVIL_BACKUP_KEEP_DAYS`). On Unraid, schedule it with the User Scripts plugin.

Restore Daredevil:

```bash
gunzip -c /mnt/user/Backups/daredevil/db_YYYYMMDD_HHMM.sql.gz | docker compose exec -T db psql -U daredevil daredevil
```

Restore one of the apps (Sonarr shown) into a fresh database:

```bash
docker compose stop sonarr
docker compose exec -T db psql -U daredevil -d postgres -c 'DROP DATABASE "sonarr-main"' -c 'CREATE DATABASE "sonarr-main"'
gunzip -c /mnt/user/Backups/daredevil/sonarr_YYYYMMDD_HHMM.sql.gz | docker compose exec -T db psql -U daredevil sonarr-main
docker compose start sonarr
```

---

## Project structure

```
daredevil/
├── apps/
│   ├── arr/            # Sonarr / Radarr / Prowlarr clients, Settings tabs, Search, webhooks
│   ├── downloads/      # Download queue + imports (read from Sonarr/Radarr)
│   ├── media_tracker/  # Dashboard, TV shows (Sonarr) and movies (Radarr) pages
│   └── qbt/            # qBittorrent client, Torrent Manager, file browser
├── config/             # Django settings, URLs, Celery config
├── templates/          # Jinja-style Django templates (Tailwind + Alpine.js)
├── static/             # Static assets
├── manage.py
├── requirements.txt
└── start.sh            # Dev launcher (macOS/Linux)
```

---

## Updating

```bash
git pull
source venv/bin/activate   # (or venv\Scripts\activate on Windows)
pip install -r requirements.txt
python manage.py migrate
```
