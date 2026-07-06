ARG DOCKERHUB_REGISTRY=docker.io
ARG NPM_CONFIG_REGISTRY

FROM ${DOCKERHUB_REGISTRY}/library/node:22-alpine AS builder
ARG NPM_CONFIG_REGISTRY
WORKDIR /app

COPY package*.json ./
RUN if [ -n "${NPM_CONFIG_REGISTRY}" ]; then \
        NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY}" npm ci; \
    else \
        npm ci; \
    fi

COPY . .
RUN npm run build

FROM ${DOCKERHUB_REGISTRY}/library/nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
