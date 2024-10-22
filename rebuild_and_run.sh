#!/bin/bash

# Stop and remove the existing container if it's running
docker stop when2meet-comparison || true
docker rm when2meet-comparison || true

# Rebuild the Docker image
docker build -t when2meet-comparison .

# Run the new container
docker run -d --name when2meet-comparison -p 5000:5000 when2meet-comparison

# Follow the logs
docker logs -f when2meet-comparison
