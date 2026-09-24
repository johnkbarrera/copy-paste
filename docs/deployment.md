# Deployment Guide

## Overview

This guide explains how to deploy **Copy Paste** to a VPS and keep it updated from the `main` branch.

The application is a FastAPI app served by Uvicorn on port `8085`. In production it should run behind Nginx, which publishes the domain and handles HTTPS.

Target domain:

```text
copy-paste.kankunapaq.com
```

---

## What Deployment Does

| Step | Action |
|------|--------|
| 1. Pull latest code | SSH into the server and sync the repository with `main` |
| 2. Install dependencies | Run `uv sync` to install/update Python packages |
| 3. Environment check | Ensure `utilities/copy_paste.env` exists on the server |
| 4. Restart service | Run `sudo systemctl restart copy-paste` |
| 5. Health check | Confirm the systemd service is active and the app responds on port `8085` |

This project does **not** run Alembic migrations. Data is stored in MongoDB using the collections created by the app:

- `copy_paste_users`
- `copy_paste_projects`
- `copy_paste_topics`

---

## Required GitHub Secrets

If you use GitHub Actions for deployment, configure these repository secrets:

| Secret name | Description |
|-------------|-------------|
| `SERVER_IP` | Public IP address of your VPS |
| `SERVER_USER` | Linux username used to SSH into the server, for example `ubuntu` |
| `SSH_PRIVATE_KEY` | Private SSH key that has access to the server |

### Where to configure them

1. Open your GitHub repository.
2. Go to **Settings** -> **Secrets and variables** -> **Actions**.
3. Click **New repository secret**.
4. Add each secret with the exact name shown above.

---

## How to Get Each Value

### `SERVER_IP`

Find the public IP in your VPS provider dashboard.

You can also check it from the server:

```bash
curl ifconfig.me
```

### `SERVER_USER`

Common defaults:

| Provider | Default user |
|----------|--------------|
| AWS EC2 Ubuntu | `ubuntu` |
| DigitalOcean | `root` or your created user |
| Hetzner | `root` or your created user |

Check the current user on the server:

```bash
whoami
```

### `SSH_PRIVATE_KEY`

Generate a dedicated deployment key on your local machine:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/copy_paste_deploy -C "copy-paste-deploy"
```

This creates:

| File | Purpose |
|------|---------|
| `~/.ssh/copy_paste_deploy` | Private key. Paste this into GitHub as `SSH_PRIVATE_KEY` |
| `~/.ssh/copy_paste_deploy.pub` | Public key. Add this to the VPS |

Add the public key to the server:

```bash
ssh-copy-id -i ~/.ssh/copy_paste_deploy.pub ubuntu@<SERVER_IP>
```

Or manually:

```bash
cat ~/.ssh/copy_paste_deploy.pub | ssh ubuntu@<SERVER_IP> "cat >> ~/.ssh/authorized_keys"
```

Verify it exists on the server:

```bash
cat ~/.ssh/authorized_keys
```

Add the private key to GitHub:

```bash
cat ~/.ssh/copy_paste_deploy
```

Copy the full output, including the `BEGIN OPENSSH PRIVATE KEY` and `END OPENSSH PRIVATE KEY` lines.

---

## Server Prerequisites

### 1. Clone the repository

Recommended location:

```bash
mkdir -p ~/open_projects
cd ~/open_projects
git clone <YOUR_REPO_URL> copy-paste
cd copy-paste
```

### 2. Install `uv`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Reload your shell or use the full path:

```bash
~/.local/bin/uv --version
```

### 3. Install dependencies

```bash
cd ~/open_projects/copy-paste
~/.local/bin/uv sync
```

### 4. Create the environment file

The real environment file is intentionally ignored by git.

```bash
cd ~/open_projects/copy-paste
cp utilities/copy_paste.env.example utilities/copy_paste.env
nano utilities/copy_paste.env
```

Minimum required values:

```env
APP_NAME=Copy Paste
DEBUG=false
MONGO_URI=mongodb+srv://usuario:password@cluster.mongodb.net/
MONGO_DATABASE=copy_paste
SECRET_KEY=replace_this_with_a_long_random_secret_key
```

### 5. Create the systemd service

```bash
sudo nano /etc/systemd/system/copy-paste.service
```

Paste:

```ini
[Unit]
Description=Copy Paste FastAPI app
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/open_projects/copy-paste
Environment=UV_CACHE_DIR=/home/ubuntu/open_projects/copy-paste/.uv-cache
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/.local/bin/uv run uvicorn copy_paste_app.main:app --host 127.0.0.1 --port 8085 --env-file utilities/copy_paste.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Adjust `User`, `WorkingDirectory`, and the `uv` path if your server uses a different username.

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable copy-paste
sudo systemctl start copy-paste
sudo systemctl status copy-paste
```

Verify Uvicorn is listening on `127.0.0.1:8085`:

```bash
sudo ss -tlnp | grep 8085
```

View logs:

```bash
sudo journalctl -u copy-paste -f
```

---

## Manual Deploy

Run this on the server:

```bash
cd ~/open_projects/copy-paste
git fetch origin
git reset --hard origin/main
~/.local/bin/uv sync
sudo systemctl restart copy-paste
sudo systemctl status copy-paste
```

Check the local app:

```bash
curl -I http://127.0.0.1:8085/
curl http://127.0.0.1:8085/
```

---

## GitHub Actions Deploy Script

Create `.github/workflows/deploy.yml` if you want automatic deployment on every push to `main`:

```yaml
name: Deploy Copy Paste

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      - name: Deploy over SSH
        uses: appleboy/ssh-action@v1.0.3
        with:
          host: ${{ secrets.SERVER_IP }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            set -e
            cd ~/open_projects/copy-paste
            git fetch origin
            git reset --hard origin/main
            ~/.local/bin/uv sync
            sudo systemctl restart copy-paste
            sleep 3
            sudo systemctl is-active --quiet copy-paste || (sudo journalctl -u copy-paste -n 50 --no-pager && exit 1)
            curl -fsS http://127.0.0.1:8085/ > /dev/null
```

For this workflow to restart the service, the deploy user must be allowed to run:

```bash
sudo systemctl restart copy-paste
sudo systemctl is-active copy-paste
sudo journalctl -u copy-paste
```

You can configure passwordless sudo for only these commands if needed.

---

## Trigger

After setup, deploy by pushing to `main`:

```bash
git push origin main
```

Monitor the result in the GitHub repository under the **Actions** tab.
