# Copy Paste

A modern, lightning-fast workspace for organizing projects, topics, and live code notes with instant auto-save and 1-click clipboard workflow.

---

## Features

- ⚡ **Instant Auto-Save**: Real-time debounce auto-saving powered by FastAPI & MongoDB.
- 🎨 **Modern Design & Themes**: Curated dark and light modes with seamless instant toggle and high-contrast readability.
- 📋 **Copy-Paste Optimized**: 1-click copy-all clipboard button, live word/line/char stats, and clean URL sharing.
- ⌨️ **Developer Ergonomics**: Tab indentation support (2 spaces) and `⌘S` / `Ctrl+S` quick save shortcut.
- 🔍 **Real-Time Filtering**: Instant client-side search across all projects and topics.
- 🔒 **Clean Authentication**: Lightweight cookie-based session authentication with PBKDF2 password hashing.

---

## Quick Start (Local Development)

### 1. Requirements

- Python `3.11` to `3.12`
- [`uv`](https://docs.astral.sh/uv/) package manager
- Running MongoDB instance (local or MongoDB Atlas)

### 2. Run the Server

```bash
cp utilities/copy_paste.env.example utilities/copy_paste.env
UV_CACHE_DIR=.uv-cache uv run uvicorn copy_paste_app.main:app --host 0.0.0.0 --port 8085 --env-file utilities/copy_paste.env
```

Once running, navigate to:
```text
http://localhost:8085
```

### Initial Credentials

- **Username**: `admin`
- **Password**: `admin`

---

## Production Deployment with Nginx & Systemd

Target domain example:
```text
copy-paste.kankunapaq.com
```

The application runs locally on port `8085`, and Nginx exposes the public domain using reverse proxy with SSL termination.

### 1. Server Setup

```bash
sudo apt update
sudo apt install -y nginx curl git
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Clone or copy the project into the server directory:

```bash
cd /var/www
sudo git clone <REPO_URL> copy-paste
sudo chown -R $USER:$USER /var/www/copy-paste
cd /var/www/copy-paste
uv sync
```

Create your local environment file from the template:
```bash
cp utilities/copy_paste.env.example utilities/copy_paste.env
nano utilities/copy_paste.env
```

Ensure it includes at least:
```env
MONGO_URI=mongodb+srv://...
MONGO_DATABASE=copy_paste
SECRET_KEY=replace_this_with_a_long_random_secret_key
```

### 2. Create Systemd Service

Create the service file:

```bash
sudo nano /etc/systemd/system/copy-paste.service
```

Add the following configuration:

```ini
[Unit]
Description=Copy Paste FastAPI Service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/var/www/copy-paste
Environment=UV_CACHE_DIR=/var/www/copy-paste/.uv-cache
ExecStart=/usr/local/bin/uv run uvicorn copy_paste_app.main:app --host 127.0.0.1 --port 8085 --env-file utilities/copy_paste.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> **Note**: Verify your `uv` binary location using `which uv` and adjust `ExecStart` if installed in `~/.cargo/bin/uv` or `/root/.cargo/bin/uv`.

Enable and start the service:

```bash
sudo chown -R www-data:www-data /var/www/copy-paste
sudo systemctl daemon-reload
sudo systemctl enable copy-paste
sudo systemctl start copy-paste
sudo systemctl status copy-paste
```

To follow live logs:

```bash
sudo journalctl -u copy-paste -f
```

### 3. Configure Nginx Reverse Proxy

Create the Nginx server block:

```bash
sudo nano /etc/nginx/sites-available/copy-paste.kankunapaq.com
```

Configuration:

```nginx
server {
    listen 80;
    server_name copy-paste.kankunapaq.com;

    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8085;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/copy-paste.kankunapaq.com /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 4. Configure DNS

Add an `A` record in your DNS provider:

```text
copy-paste.kankunapaq.com -> <YOUR_SERVER_IP>
```

Test connection:

```bash
curl -I http://copy-paste.kankunapaq.com
```

### 5. Enable HTTPS with Let's Encrypt (Certbot)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d copy-paste.kankunapaq.com
```

Test automatic certificate renewal:

```bash
sudo certbot renew --dry-run
```

### 6. Updating Deployed Application

```bash
cd /var/www/copy-paste
sudo -u www-data git pull
sudo -u www-data uv sync
sudo systemctl restart copy-paste
sudo systemctl reload nginx
```

### 7. Useful Diagnostic Commands

```bash
sudo systemctl status copy-paste
sudo journalctl -u copy-paste -n 100
sudo nginx -t
curl -I http://127.0.0.1:8085
curl -I https://copy-paste.kankunapaq.com
```
