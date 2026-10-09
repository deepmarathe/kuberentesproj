# ---- notifications service ----
# Multi-stage build: install deps in one stage, copy only what's needed into the runtime image.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev --no-audit --no-fund

FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY package.json server.js ./
# The node image ships a non-root "node" user (uid 1000).
USER node
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
EXPOSE 8080
CMD ["node", "server.js"]
