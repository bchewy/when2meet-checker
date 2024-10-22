#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Update and upgrade the system
sudo apt update && sudo apt upgrade -y

# Install necessary packages
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/1.29.2/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Install Certbot and Nginx
sudo apt install -y certbot python3-certbot-nginx nginx

# Clone the repository (replace with your actual repository URL)
git clone https://github.com/yourusername/when2meet-comparison.git
cd when2meet-comparison

# Build the Docker image
docker build -t when2meet-comparison .

# Create a Docker Compose file
cat <<EOF >docker-compose.yml
version: '3'
services:
  web:
    image: when2meet-comparison
    ports:
      - "5000:5000"
    restart: always
EOF

# Start the Docker container
docker-compose up -d

# Configure Nginx
sudo tee /etc/nginx/sites-available/when2meet.bchwy.com <<EOF
server {
    listen 80;
    server_name when2meet.bchwy.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF

# Enable the Nginx site
sudo ln -s /etc/nginx/sites-available/when2meet.bchwy.com /etc/nginx/sites-enabled/

# Test Nginx configuration
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx

# Obtain and install SSL certificate
sudo certbot --nginx -d when2meet.bchwy.com --non-interactive --agree-tos --email your-email@example.com

# Set up automatic renewal
(
    crontab -l 2>/dev/null
    echo "0 12 * * * /usr/bin/certbot renew --quiet"
) | crontab -

echo "Setup complete! Your application should now be running at https://when2meet.bchwy.com"
