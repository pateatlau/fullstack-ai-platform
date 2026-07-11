# Node Backend: Multi-stage (TypeScript build + JS runtime)
# Stage 1: Build
FROM node:24.0.0-alpine AS builder

WORKDIR /app

# Copy lockfile and package.json first for better caching
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./

# Install dependencies (including dev)
RUN if [ -f package-lock.json ]; then npm ci --prefer-offline --no-audit; else npm install; fi

# Copy source
COPY . .

# Build TypeScript
RUN npm run build

# Stage 2: Runtime
FROM node:24.0.0-alpine

WORKDIR /app

# Create non-root user
RUN addgroup -g 1001 -S nodejs && adduser -S nodejs -u 1001

# Copy package.json for production install
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./

# Install only production dependencies
RUN if [ -f package-lock.json ]; then npm ci --omit=dev --prefer-offline --no-audit; else npm install --omit=dev; fi

# Copy compiled app from builder
COPY --from=builder /app/dist ./dist

# Change ownership
RUN chown -R nodejs:nodejs /app

USER nodejs

# Runtime environment
ENV NODE_ENV=production
ENV PORT=8000

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:8000/api/health || exit 1

CMD ["node", "dist/server.js"]
