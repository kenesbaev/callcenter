FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS dependencies
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY services/voice-gateway/package.json services/voice-gateway/package.json
COPY packages/ui/package.json packages/ui/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/config/package.json packages/config/package.json
RUN npm ci

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS builder
WORKDIR /workspace
COPY --from=dependencies /workspace/node_modules ./node_modules
COPY . .
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=${API_INTERNAL_URL}
RUN npm run build --workspace @teamora/web

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS runtime
ENV NODE_ENV=production
RUN addgroup -S teamora && adduser -S teamora -G teamora
WORKDIR /app
COPY --from=builder /workspace/apps/web/.next/standalone ./
COPY --from=builder /workspace/apps/web/.next/static ./apps/web/.next/static
USER teamora
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
