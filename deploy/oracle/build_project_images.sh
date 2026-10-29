#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for item in '1.26.4 oracle-numpy-1264' '2.0.2 oracle-numpy-202'; do
    set -- $item
    docker build --pull -f "$here/Dockerfile.project-numpy" \
        --build-arg "NUMPY_VERSION=$1" -t "$2" "$here"
    docker image inspect --format '{{.Id}}' "$2"
    docker run --rm --network none "$2" sha256sum /opt/reprodelta-environment.lock
done
echo 'Record the image IDs and matching environment lock hashes in the new profile approval.'
