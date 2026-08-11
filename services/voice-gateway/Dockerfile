FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS build
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY packages/config/package.json packages/config/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/ui/package.json packages/ui/package.json
COPY apps/web/package.json apps/web/package.json
COPY services/voice-gateway/package.json services/voice-gateway/package.json
RUN npm ci
COPY packages/config packages/config
COPY packages/contracts packages/contracts
COPY services/voice-gateway services/voice-gateway
RUN npm run build --workspace @teamora/voice-gateway

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS runtime
ENV NODE_ENV=production
WORKDIR /workspace
COPY --from=build /workspace/node_modules ./node_modules
COPY --from=build /workspace/packages ./packages
COPY --from=build /workspace/services/voice-gateway/package.json ./services/voice-gateway/package.json
COPY --from=build /workspace/services/voice-gateway/node_modules ./services/voice-gateway/node_modules
COPY --from=build /workspace/services/voice-gateway/dist ./services/voice-gateway/dist
USER node
CMD ["node", "services/voice-gateway/dist/index.js"]
