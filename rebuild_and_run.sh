#!/bin/bash

# Stop and remove the existing container if it's running
docker stop when2meet || true
docker rm when2meet || true

# Rebuild the Docker image
docker build -t when2meet .

# Run the new container
docker run -d --name when2meet -p 5000:5000 when2meet

# Follow the logs
docker logs -f when2meet
