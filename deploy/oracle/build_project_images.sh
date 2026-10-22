#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for item in '1.26.4 oracle-numpy-1264' '2.0.2 oracle-numpy-202'; do
    set -- $item
    docker build --pull -f "$here/Dockerfile.project-numpy" \
        --build-arg "NUMPY_VERSION=$1" -t "$2" "$here"
    docker image inspect --format '{{.Id}}' "$2"
done
echo 'Copy the two image IDs into ORACLE_OLD_IMAGE_ID and ORACLE_NEW_IMAGE_ID.'
