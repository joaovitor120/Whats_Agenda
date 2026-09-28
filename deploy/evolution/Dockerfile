FROM evoapicloud/evolution-api:v2.3.7@sha256:1bd8afc4a6cf48822e6cf02469aeae7bd35a12a6b616eacd1291926307f4d339
USER root
COPY patch-cors.cjs /tmp/patch-cors.cjs
RUN node /tmp/patch-cors.cjs
RUN mkdir -p /evolution/instances && chown -R node:node /evolution/prisma /evolution/instances
COPY --chown=node:node start.sh /evolution/start.sh
USER node
ENTRYPOINT ["/bin/sh", "/evolution/start.sh"]
