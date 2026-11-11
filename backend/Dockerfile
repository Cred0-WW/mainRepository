# Сборка сервера в отдельном этапе.
FROM golang:1.22-alpine AS builder
WORKDIR /build
COPY backend/go.mod backend/go.sum ./
RUN go mod download
COPY backend/main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/app .

# В рабочем образе только приложение и файлы страницы.
FROM gcr.io/distroless/static-debian12:nonroot
WORKDIR /app
COPY --from=builder /out/app /app/app
COPY frontend/ /app/static/
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/app/app"]
