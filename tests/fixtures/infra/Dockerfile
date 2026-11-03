# Planted: floating base image, runs as root. (No HEALTHCHECK either, but docker-no-healthcheck
# stays quiet here because the k8s workload YAML in this tree means probes are used instead.)
FROM node:latest
WORKDIR /app
COPY . .
RUN npm ci
EXPOSE 8080
CMD ["node", "server.js"]
