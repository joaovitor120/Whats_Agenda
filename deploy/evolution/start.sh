#!/bin/sh
set -eu
cd /evolution
# The upstream wrapper prints a database URL; run its migration command directly.
npm run db:deploy
exec npm run start:prod
