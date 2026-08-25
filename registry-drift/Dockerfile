# registry-drift - public-registry gateway for the cloud fraud demo.
# No dependencies, so there is nothing to install: just copy the source and run.
FROM node:22-alpine

WORKDIR /app
COPY package.json ./
COPY server.js records.js ./

# Cloud Run provides $PORT (default 8080). The server reads process.env.PORT.
ENV PORT=8080
EXPOSE 8080

# REVOKE_TOKEN must be supplied at deploy time (env or Secret Manager); never baked in.
CMD ["node", "server.js"]
