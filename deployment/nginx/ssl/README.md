# SSL / TLS Certificates Directory

This directory stores the SSL certificates for the SeniorLife OS Nginx reverse proxy.

## Default Certificates
- `cert.pem`: Public certificate (pre-populated with a self-signed certificate for immediate out-of-the-box startup).
- `key.pem`: Private key.

## Replacing with Production Certificates (e.g. Let's Encrypt / Custom CA)

### Option 1: Copy your certificates directly
Replace `cert.pem` and `key.pem` with your domain's files:
```bash
cp /path/to/your/fullchain.pem cert.pem
cp /path/to/your/privkey.pem key.pem
```

Then reload Nginx:
```bash
docker compose exec nginx nginx -s reload
```

### Option 2: Using Certbot (Let's Encrypt)
Run Certbot using webroot verification:
```bash
docker run -it --rm --name certbot \
  -v seniorlife_certbot_www:/var/www/certbot \
  -v /etc/letsencrypt:/etc/letsencrypt \
  certbot/certbot certonly --webroot \
  -w /var/www/certbot \
  -d yourdomain.com
```
Then copy or symlink the resulting `/etc/letsencrypt/live/yourdomain.com/fullchain.pem` to `cert.pem` and `privkey.pem` to `key.pem`.
