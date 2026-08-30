# syntax=docker/dockerfile:1
# Build linux/amd64 from the repository root so npm uses the one workspace lock:
#   docker buildx build --platform linux/amd64 -f App/Dockerfile --load .
# The official Node tag is pinned to its verified linux/amd64 OCI manifest.

FROM node:22.19.0-alpine3.22@sha256:704b199e36b5c1bc505da773f742299dc1ee5a4c70b86d1eb406c334f63253c6 AS dependencies
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY App/package.json ./App/package.json
COPY packages/sdk/package.json ./packages/sdk/package.json
COPY packages/cli/package.json ./packages/cli/package.json
RUN npm ci --no-audit --no-fund

FROM node:22.19.0-alpine3.22@sha256:704b199e36b5c1bc505da773f742299dc1ee5a4c70b86d1eb406c334f63253c6 AS builder
WORKDIR /workspace
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /workspace ./
COPY . .
RUN npm run build:next --workspace=App

FROM node:22.19.0-alpine3.22@sha256:704b199e36b5c1bc505da773f742299dc1ee5a4c70b86d1eb406c334f63253c6 AS runtime
LABEL io.instadescribe.runtime="next-standalone"
LABEL io.instadescribe.base-digest="sha256:704b199e36b5c1bc505da773f742299dc1ee5a4c70b86d1eb406c334f63253c6"

RUN addgroup -S -g 10001 nodejs \
    && adduser -S -u 10001 -G nodejs nextjs

WORKDIR /workspace
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    HOME=/tmp

COPY --from=builder --chown=nextjs:nodejs /workspace/App/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /workspace/App/.next/static ./App/.next/static
COPY --from=builder --chown=nextjs:nodejs /workspace/App/public ./App/public

RUN mkdir -p /workspace/App/.next/cache /tmp \
    && chown -R nextjs:nodejs /workspace/App/.next/cache /tmp \
    && if find /workspace \( -name '.env*' -o -name '*.mp4' -o -name '*.mp3' \
      -o -iname '*.pem' -o -iname '*.key' -o -iname '*.p12' -o -iname '*.pfx' \) \
      -print | grep .; then \
      echo "forbidden secret or media asset baked into Next runtime"; exit 1; \
    fi

WORKDIR /workspace/App
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node", "server.js"]
