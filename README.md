# Daredevil

A self-hosted media manager that integrates [TMDB](https://www.themoviedb.org/) for metadata and [qBittorrent](https://www.qbittorrent.org/) for downloads. Track TV shows and movies, search for torrents, manage downloads, and automatically organise completed files into a Plex-ready folder structure.

---

## Features

- **Media tracking** — search TMDB for movies and TV shows, monitor for new episodes, track download status
- **Download queue** — real-time progress synced from qBittorrent, auto-search and download
- **File organisation** — moves completed downloads to Plex-standard folder structure (`Movie (Year)/`, `Show (Year)/Season 01/`)
- **File browser** — browse category folders directly from the UI
- **qBittorrent integration** — torrent search (via qBT plugins), category management, settings

---

## Requirements

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.11+ | Not needed if using Docker |
| Redis | 7+ | Required for background tasks (Celery) — included in Docker |
| qBittorrent | 4.6+ | Web UI must be enabled |

---

## Installation

### Docker (recommended)

Docker Compose runs the full stack — Daredevil, qBittorrent, Plex, Redis, and Celery — in one command. Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```
┌─────────────┐     ┌──────────────┐     ┌──────────┐
│  Daredevil  │────▶│ qBittorrent  │     │   Plex   │
│  :8000      │     │  :8080       │     │  :32400  │
└──────┬──────┘     └──────┬───────┘     └────┬─────┘
       │                   │                   │
       │  downloads volume │                   │
       │◀──────────────────┘                   │
       │                                       │
       │  movies + tv volumes                  │
       └──────────────────────────────────────▶│
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

# 4. Create a Daredevil admin user
docker compose exec web python manage.py createsuperuser
```

| Service | URL |
|---------|-----|
| Daredevil | http://localhost:8000 |
| qBittorrent | http://localhost:8080 |
| Jackett | http://localhost:9117 |
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

> To create the admin user via the Docker Desktop GUI: open the `web` container → **Exec** tab → run `python manage.py createsuperuser` and follow the prompts. You can then log in at http://localhost:8000/admin.

---

**Set up qBittorrent after first run:**

1. Log in at http://localhost:8080 with username `admin` and the temporary password from the logs
2. Go to Tools → Options → Web UI and set a permanent password
3. Update `QBITTORRENT_PASSWORD` in `.env` to match, then `docker compose restart web worker`

**Set up Jackett:**

Jackett gives qBittorrent access to hundreds of torrent indexers through a single search interface.

1. Open http://localhost:9117 and click **+ Add Indexer** to add your preferred sites (e.g. 1337x, YTS, RARBG mirrors, etc.)
2. Copy your **API Key** from the top-right of the Jackett dashboard
3. In qBittorrent → Tools → Options → **Search** → tick *Search enabled* 
4. Install the Jackett search plugin for qBittorrent:
   - Download [`jackett_qbittorrent.py`](https://github.com/qbittorrent/search-plugins/wiki/Unofficial-search-plugins#jackett) (or use the `jack.py` one from the Jackett repo)
   - In qBittorrent → Search → **Search Plugins** → Install a new one → select the `.py` file
   - Configure the plugin with:
     - URL: `http://jackett:9117` (Docker internal hostname)
     - API Key: (from Jackett dashboard)
5. Run a test search in qBittorrent to confirm Jackett is returning results
6. Daredevil's auto-search and torrent search page will now use Jackett-backed results

**Set up Daredevil categories:**

In Daredevil → Categories, configure paths using the container-internal paths:

| Field | Value |
|-------|-------|
| Download Path | `/media/downloads` |
| Completed Path (movies) | `/media/movies` |
| Completed Path (TV) | `/media/tv` |

**Set up Plex libraries:**

In Plex → Libraries → Add Library, point it at:
- Movies → `/data/movies`
- TV Shows → `/data/tv`

---

**Useful commands:**

```bash
docker compose logs -f web          # Daredevil logs
docker compose logs -f qbittorrent  # qBittorrent logs
docker compose logs -f jackett      # Jackett logs
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
| qBittorrent | http://tower:8081 (note: Unraid WebUI uses 8080) |
| Jackett | http://tower:9118 |
| Plex | http://tower:32400/web |
| PostgreSQL | internal only (not exposed) |

> **Port note:** Unraid's own WebUI runs on port 8080, so qBittorrent is mapped to **8081** in this stack to avoid the conflict.

**Updating:**

```bash
cd /mnt/user/appdata/daredevil
git pull
docker-compose build web worker beat
docker-compose up -d
```

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

# Default download path (fallback if no category path is set)
DOWNLOAD_PATH=/path/to/downloads
```

---

## qBittorrent setup

1. Open qBittorrent → **Tools → Options → Web UI** → enable Web UI, set a username/password
2. In Daredevil, go to **qBT Settings** and verify the connection
3. Go to **Categories** and create categories for movies and TV (e.g. `movies`, `tv-shows`)
4. Set a **Download Path** (where qBT saves in-progress files) and a **Completed Path** (where Daredevil moves finished files)

> If qBittorrent runs on a different machine, set `QBITTORRENT_HOST` to that machine's hostname or IP.  
> The **Download Path** and **Completed Path** in Categories must be paths that **Daredevil itself can read/write** — use network mounts (SMB/NFS) if qBT and Daredevil are on separate machines.

---

## Folder structure for Plex

Daredevil organises completed files automatically:

```
Movies/
  Inception (2010)/
    Inception.2010.1080p.mkv

TV/
  Breaking Bad (2008)/
    Season 01/
      E01-Pilot/
        Breaking.Bad.S01E01.mkv
```

---

## Background tasks (Celery)

Celery handles:
- Syncing TV show metadata from TMDB
- Auto-queuing new episodes when they air
- Checking movie digital release dates

If Redis is not running, Daredevil still works for manual searches and downloads — background automation is just disabled.

---

## Project structure

```
daredevil/
├── apps/
│   ├── downloads/      # Download queue, file moves
│   ├── media_tracker/  # TMDB sync, movies, TV shows, episodes
│   └── qbt/            # qBittorrent client, categories, file browser
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
