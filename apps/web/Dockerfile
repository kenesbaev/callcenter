FROM node:22-alpine AS dependencies
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY services/voice-gateway/package.json services/voice-gateway/package.json
COPY packages/ui/package.json packages/ui/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/config/package.json packages/config/package.json
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /workspace
COPY --from=dependencies /workspace/node_modules ./node_modules
COPY . .
RUN npm run build --workspace @teamora/web

FROM node:22-alpine AS runtime
ENV NODE_ENV=production
RUN addgroup -S teamora && adduser -S teamora -G teamora
WORKDIR /app
COPY --from=builder /workspace/apps/web/.next/standalone ./
COPY --from=builder /workspace/apps/web/.next/static ./apps/web/.next/static
USER teamora
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
