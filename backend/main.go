package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"time"

	_ "github.com/lib/pq"
)

type item struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func main() {
	port := env("HTTP_PORT", "8080")
	if len(os.Args) == 2 && os.Args[1] == "--healthcheck" {
		client := &http.Client{Timeout: 2 * time.Second}
		response, err := client.Get("http://127.0.0.1:" + port + "/healthz")
		if err != nil {
			os.Exit(1)
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		return
	}

	dsn := &url.URL{
		Scheme: "postgres",
		User:   url.UserPassword(os.Getenv("DB_USER"), os.Getenv("DB_PASSWORD")),
		Host:   net.JoinHostPort(env("DB_HOST", "db"), env("DB_PORT", "5432")),
		Path:   "/" + os.Getenv("DB_NAME"),
	}
	query := url.Values{"sslmode": {"disable"}, "connect_timeout": {"5"}}
	dsn.RawQuery = query.Encode()
	db, err := sql.Open("postgres", dsn.String())
	if err != nil {
		log.Fatal("database configuration error")
	}
	defer db.Close()
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(5 * time.Minute)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	err = db.PingContext(ctx)
	cancel()
	if err != nil {
		log.Fatal("database is unavailable")
	}
	log.Print("connected to PostgreSQL")

	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/items", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		rows, err := db.QueryContext(ctx, "SELECT id, name FROM items ORDER BY id")
		if err != nil {
			http.Error(w, "database query failed", http.StatusServiceUnavailable)
			return
		}
		defer rows.Close()
		items := make([]item, 0)
		for rows.Next() {
			var value item
			if err := rows.Scan(&value.ID, &value.Name); err != nil {
				http.Error(w, "could not read item", http.StatusInternalServerError)
				return
			}
			items = append(items, value)
		}
		if rows.Err() != nil {
			http.Error(w, "could not read items", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		if err := json.NewEncoder(w).Encode(items); err != nil {
			log.Print("could not write response")
		}
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), time.Second)
		defer cancel()
		if err := db.PingContext(ctx); err != nil {
			http.Error(w, "database unavailable", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		fmt.Fprintln(w, "ok")
	})
	mux.Handle("GET /", http.FileServer(http.Dir(env("STATIC_DIR", "/app/static"))))
	server := &http.Server{
		Addr:              ":" + port,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Printf("listening on :%s", port)
	log.Fatal(server.ListenAndServe())
}
