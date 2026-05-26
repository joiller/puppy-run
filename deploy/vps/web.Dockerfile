FROM node:22-slim AS build

WORKDIR /app

COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/ ./

ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

FROM caddy:2-alpine

COPY deploy/vps/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv/puppyrun
