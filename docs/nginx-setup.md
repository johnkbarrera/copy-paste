# Nginx + HTTPS Setup Guide

Deploy Copy Paste at:

```text
https://copy-paste.kankunapaq.com/
```

Nginx will proxy public traffic to Uvicorn running locally on port `8085`.

---

## Architecture

```text
Internet
   |
   v
Nginx on ports 80 / 443          handles SSL and domain routing
   |
   v
Uvicorn on 127.0.0.1:8085        runs the FastAPI app
```

The app should listen only on `127.0.0.1:8085` in production. Do not expose port `8085` directly to the internet.

---

## Prerequisites

- Ubuntu VPS with a public IP
- DNS `A` record for `copy-paste.kankunapaq.com` pointing to the VPS IP
- Copy Paste running as the `copy-paste` systemd service
- Environment file created at `utilities/copy_paste.env`

See [deployment.md](deployment.md) for the app and systemd setup.

---

## Step 1 - Verify DNS

Before installing SSL, confirm the domain resolves to your server:

```bash
dig +short copy-paste.kankunapaq.com
```

Or:

```bash
nslookup copy-paste.kankunapaq.com
```

Expected result: your VPS public IP.

DNS changes can take time to propagate. Do not continue to Certbot until the domain resolves correctly.

---

## Step 2 - Install Nginx

```bash
sudo apt update
sudo apt install nginx -y
```

Verify it is running:

```bash
sudo systemctl status nginx
```

---

## Step 3 - Confirm the App Service

The app must listen on `127.0.0.1:8085`.

Check the service:

```bash
sudo systemctl status copy-paste
sudo ss -tlnp | grep 8085
```

Expected listener:

```text
127.0.0.1:8085
```

Test the app locally from the server:

```bash
curl -I http://127.0.0.1:8085/
curl http://127.0.0.1:8085/
```

If the service is not running, check:

```bash
sudo journalctl -u copy-paste -n 100 --no-pager
```

If port `8085` is already in use when running Uvicorn manually, that usually means the `copy-paste` service is already active. Check with:

```bash
sudo systemctl status copy-paste
sudo ss -tlnp | grep 8085
```

Only one process can listen on `127.0.0.1:8085`.

---

## Step 4 - Configure Nginx

Create a site config:

```bash
sudo nano /etc/nginx/sites-available/copy-paste
```

Paste:

```nginx
server {
    listen 80;
    server_name copy-paste.kankunapaq.com;

    client_max_body_size 20M;

    location / {
        proxy_pass         http://127.0.0.1:8085;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/copy-paste /etc/nginx/sites-enabled/
sudo nginx -t
```

Expected output:

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

Reload Nginx:

```bash
sudo systemctl reload nginx
```

Test HTTP before SSL:

```bash
curl -I http://copy-paste.kankunapaq.com/
curl http://copy-paste.kankunapaq.com/
```

---

## Step 5 - Install HTTPS with Certbot

```bash
sudo apt install certbot python3-certbot-nginx -y
```

Request a certificate:

```bash
sudo certbot --nginx -d copy-paste.kankunapaq.com
```

Certbot will:

1. Verify domain ownership with an HTTP challenge.
2. Issue a Let's Encrypt certificate.
3. Update the Nginx config for HTTPS.

When prompted, choose the redirect option so HTTP redirects to HTTPS.

Verify the certificate:

```bash
sudo certbot certificates
```

---

## Step 6 - Verify Final Nginx Config

After Certbot, `/etc/nginx/sites-available/copy-paste` should be equivalent to:

```nginx
server {
    listen 80;
    server_name copy-paste.kankunapaq.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name copy-paste.kankunapaq.com;

    ssl_certificate     /etc/letsencrypt/live/copy-paste.kankunapaq.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/copy-paste.kankunapaq.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 20M;

    location / {
        proxy_pass         http://127.0.0.1:8085;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        "upgrade";
    }
}
```

If Certbot did not add the redirect automatically, edit the file manually and reload Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## Step 7 - Final Verification

```bash
# HTTPS works
curl -I https://copy-paste.kankunapaq.com/

# HTTP redirects to HTTPS
curl -I http://copy-paste.kankunapaq.com/

# Local app still works
curl -I http://127.0.0.1:8085/
```

The HTTPS request should return `200` or `303` depending on session/login state. The HTTP request should return `301 Moved Permanently` after SSL redirect is enabled.

---

## Certificate Renewal

Let's Encrypt certificates expire every 90 days. Certbot installs automatic renewal.

Test renewal:

```bash
sudo certbot renew --dry-run
```

---

## Useful Troubleshooting Commands

```bash
sudo systemctl status copy-paste
sudo journalctl -u copy-paste -f
sudo systemctl status nginx
sudo nginx -t
sudo tail -n 100 /var/log/nginx/error.log
sudo ss -tlnp | grep 8085
```
