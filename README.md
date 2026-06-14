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

The easiest way to run Daredevil on any OS. Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
# 1. Clone the repo
git clone https://github.com/jamierossnz-lang/daredevil.git
cd daredevil

# 2. Create your .env file
cp .env.example .env
# Edit .env — set TMDB_API_KEY, QBITTORRENT_HOST/PORT/USER/PASS at minimum

# 3. (Optional) Mount your media folders
# Edit docker-compose.yml and uncomment the volume lines under the web + worker services.
# Point them at your actual download/movie/TV folders.

# 4. Start everything
docker compose up -d

# 5. Create an admin user
docker compose exec web python manage.py createsuperuser
```

Open [http://localhost:8000](http://localhost:8000).

**Useful commands:**

```bash
docker compose logs -f web          # live app logs
docker compose logs -f worker       # Celery worker logs
docker compose restart web          # restart after config change
docker compose down                 # stop everything
docker compose down -v              # stop + delete database (destructive!)
```

**Updating:**

```bash
git pull
docker compose build
docker compose up -d
```

**Media folders on Windows (Docker Desktop):**

In `docker-compose.yml`, uncomment and set the volume paths using forward slashes:

```yaml
volumes:
  - D:/Downloads:/media/downloads
  - D:/Movies:/media/movies
  - D:/TV:/media/tv
```

Then in Daredevil's Categories page, set paths to `/media/downloads`, `/media/movies`, `/media/tv`.

> **Note on SQLite + Docker:** The database is stored in a named Docker volume (`db_data`). It's shared between the web, worker, and beat containers using file locking. This works fine for personal/single-user use. For high-concurrency production deployments, switch to PostgreSQL.

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
