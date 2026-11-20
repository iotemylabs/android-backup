#!/bin/bash
# Build this fork's image and push it to GitHub's registry. Run it on any machine with Docker,
# after `docker login ghcr.io`. Upstream's own script, written for its server, is contrib/deploy-tower.sh.
#
# It does not touch a server: afterwards, update the container from Unraid's Docker page
# (the template is unraid/android-backup.xml).
set -euo pipefail
cd "$(dirname "$0")"
IMG=${IMG:-ghcr.io/iotemylabs/android-backup}
TAG=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' android_backup/__init__.py)

docker build -t "$IMG:latest" -t "$IMG:$TAG" .
docker run --rm "$IMG:$TAG" adb version >/dev/null  # the image starts and adb is in it
docker push "$IMG:$TAG"
docker push "$IMG:latest"
echo "pushed $IMG:$TAG and $IMG:latest"
