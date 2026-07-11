# Frontend: Multi-stage build + static serve
# Stage 1: Build
FROM node:24.0.0-alpine AS builder

WORKDIR /app

# Copy lockfile and package.json first for better caching
COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./

# Install dependencies (including dev for build)
RUN if [ -f package-lock.json ]; then npm ci --prefer-offline --no-audit; else npm install; fi

# Copy source
COPY . .

# Build
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# Stage 2: Runtime (nginx-alpine slim for static serving)
FROM nginx:1.27-alpine

WORKDIR /usr/share/nginx/html

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy built assets from builder
COPY --from=builder /app/dist .

# Copy nginx config
COPY nginx.conf /etc/nginx/nginx.conf

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:80/ || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
