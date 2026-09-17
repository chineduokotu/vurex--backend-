# Comprehensive Guide: Configuring HTTPS (SSL) for Vurex EC2 Backend & Custom Domain

This guide provides step-by-step instructions to convert your AWS EC2 Django REST API (`http://13.49.183.176`) to a secure **HTTPS** endpoint (`https://api.yourdomain.com`) using a custom domain, Nginx, and free SSL certificates from Let's Encrypt (Certbot).

---

## 🏗️ Target Production Architecture

```
                               ┌─────────────────────────┐
                               │     User's Browser      │
                               └────────────┬────────────┘
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     │                                             │
                     ▼ HTTPS (Port 443)                            ▼ HTTPS (Port 443)
┌────────────────────────────────────────┐     ┌────────────────────────────────────────┐
│  Frontend (React App)                  │     │  Backend (Django API)                  │
│  Domain: https://yourdomain.com        │     │  Domain: https://api.yourdomain.com    │
│  Hosted: Vercel / Netlify              │     │  Hosted: AWS EC2 (13.49.183.176)       │
└────────────────────────────────────────┘     └───────────────────┬────────────────────┘
                                                                   │
                                                                   ▼
                                                       ┌────────────────────────┐
                                                       │  Supabase PostgreSQL   │
                                                       └────────────────────────┘
```

---

## Step 1: Set Up DNS Records (Domain Registrar)

Log into your Domain Registrar (e.g., GoDaddy, Namecheap, Cloudflare, Name.com) and add the following **DNS Records**:

| Record Type | Host / Name | Target / Points To | Purpose |
| :--- | :--- | :--- | :--- |
| **A Record** | `api` | `13.49.183.176` | Points `api.yourdomain.com` directly to your EC2 instance IP. |
| **CNAME** *(or A)* | `@` (or `www`) | `cname.vercel-dns.com` *(or Netlify URL)* | Points `yourdomain.com` to your hosted React frontend. |

> [!NOTE]
> DNS propagation typically takes between **2 to 15 minutes**. You can check if your domain has propagated by running:
> ```bash
> ping api.yourdomain.com
> ```
> It should resolve to `13.49.183.176`.

---

## Step 2: Update Nginx Configuration on EC2

1. SSH into your EC2 instance:
   ```bash
   ssh -i ~/Downloads/pem.pem ubuntu@13.49.183.176
   ```

2. Edit the Nginx site configuration:
   ```bash
   sudo nano /etc/nginx/sites-available/vurex
   ```

3. Update the `server_name` directive to match your sub-domain (`api.yourdomain.com`):
   ```nginx
   server {
       listen 80;
       server_name api.yourdomain.com 13.49.183.176;

       location = /favicon.ico { access_log off; log_not_found off; }

       location /static/ {
           alias /var/www/vurex-server/staticfiles/;
       }

       location /media/ {
           alias /var/www/vurex-server/media/;
       }

       location / {
           include proxy_params;
           proxy_pass http://unix:/run/gunicorn.sock;
       }
   }
   ```

4. Test Nginx syntax and restart Nginx:
   ```bash
   sudo nginx -t
   sudo systemctl restart nginx
   ```

---

## Step 3: Update Environment Variables in EC2 `.env`

Update your EC2 environment configuration to accept the new domain name and frontend origin:

```bash
nano /var/www/vurex-server/.env
```

Set/Update these variables:
```env
ALLOWED_HOSTS=api.yourdomain.com,13.49.183.176,127.0.0.1,localhost
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com,http://localhost:5173
```

Restart Gunicorn:
```bash
sudo systemctl restart gunicorn
```

---

## Step 4: Obtain & Install Free Let's Encrypt SSL (Certbot)

1. Install Certbot and its Nginx plugin on Ubuntu:
   ```bash
   sudo apt update
   sudo apt install certbot python3-certbot-nginx -y
   ```

2. Run Certbot to issue and auto-configure the SSL certificate:
   ```bash
   sudo certbot --nginx -d api.yourdomain.com
   ```

3. Certbot will prompt you for:
   - **Email address**: Enter your email (for urgent renewal notices).
   - **Terms of Service**: Type `Y` and press `Enter`.
   - **HTTP to HTTPS Redirect**: Choose Option `2` (Redirect all HTTP traffic to HTTPS).

---

## Step 5: Verify Auto-Renewal & Test HTTPS

1. **Verify SSL Auto-Renewal**:
   Certbot sets up an automatic systemd timer for certificate renewal (Let's Encrypt certificates renew automatically every 90 days). Test the renewal process with:
   ```bash
   sudo certbot renew --dry-run
   ```

2. **Test Backend in Browser**:
   Open your browser and visit:
   - **`https://api.yourdomain.com/`**
   - **`https://api.yourdomain.com/admin/`**

   You will see the **🔒 Padlock icon** in the address bar indicating full HTTPS security!

---

## Step 6: Update React Frontend API URL

1. In your local `vurex-client` project, update `.env`:
   ```env
   VITE_API_URL=https://api.yourdomain.com/api
   ```

2. Commit and deploy your frontend to Vercel/Netlify.

---

## 🛠️ Troubleshooting & Common Fixes

| Issue | Cause | Fix |
| :--- | :--- | :--- |
| **Certbot Error: Could not bind to IPv4 address / Domain DNS mismatch** | DNS A record has not propagated yet or points to wrong IP. | Run `ping api.yourdomain.com` to confirm it returns `13.49.183.176`. |
| **AWS Connection Timeout on Port 443** | AWS EC2 Security Group is missing HTTPS (Port 443) inbound rule. | Go to AWS Console -> EC2 -> Security Groups -> Edit Inbound Rules -> Add **HTTPS (Port 443) from 0.0.0.0/0**. |
| **Mixed Content Warning in Browser** | Frontend is on `https://` but making requests to `http://`. | Ensure `VITE_API_URL` starts strictly with `https://`. |
