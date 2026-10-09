// node-agent: runs as a DaemonSet (one pod per node) and reports that node's health.
//
// /proc/loadavg, /proc/meminfo and /proc/uptime are NOT namespaced, so an unprivileged
// container sees the whole node's values -- no hostPath, no privileges, Pod Security
// "restricted" still applies. A headless Service (clusterIP: None) lets the ops-api
// discover every agent with a single DNS lookup.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
)

var (
	node    = os.Getenv("NODE_NAME")
	pod     = os.Getenv("POD_NAME")
	version = envOr("APP_VERSION", "dev")
	started = time.Now()
	logger  = slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{ReplaceAttr: func(_ []string, a slog.Attr) slog.Attr {
		if a.Key == slog.LevelKey {
			a.Key = "severity"
		} else if a.Key == slog.MessageKey {
			a.Key = "message"
		}
		return a
	}})).With("service", "node-agent", "node", node, "pod", pod)
)

type info struct {
	Node          string  `json:"node"`
	Pod           string  `json:"pod"`
	Version       string  `json:"version"`
	CPUs          int     `json:"cpus"`
	Load1         float64 `json:"load1"`
	Load5         float64 `json:"load5"`
	Load15        float64 `json:"load15"`
	MemTotalMB    int64   `json:"mem_total_mb"`
	MemAvailMB    int64   `json:"mem_available_mb"`
	UptimeSeconds float64 `json:"node_uptime_seconds"`
	AgentUptime   float64 `json:"agent_uptime_seconds"`
}

func collect() info {
	i := info{Node: node, Pod: pod, Version: version, CPUs: runtime.NumCPU(), AgentUptime: time.Since(started).Seconds()}
	if b, err := os.ReadFile("/proc/loadavg"); err == nil {
		f := strings.Fields(string(b))
		if len(f) >= 3 {
			i.Load1, _ = strconv.ParseFloat(f[0], 64)
			i.Load5, _ = strconv.ParseFloat(f[1], 64)
			i.Load15, _ = strconv.ParseFloat(f[2], 64)
		}
	}
	if b, err := os.ReadFile("/proc/uptime"); err == nil {
		if f := strings.Fields(string(b)); len(f) > 0 {
			i.UptimeSeconds, _ = strconv.ParseFloat(f[0], 64)
		}
	}
	if fh, err := os.Open("/proc/meminfo"); err == nil {
		defer fh.Close()
		s := bufio.NewScanner(fh)
		for s.Scan() {
			f := strings.Fields(s.Text())
			if len(f) < 2 {
				continue
			}
			kb, _ := strconv.ParseInt(f[1], 10, 64)
			switch f[0] {
			case "MemTotal:":
				i.MemTotalMB = kb / 1024
			case "MemAvailable:":
				i.MemAvailMB = kb / 1024
			}
		}
	}
	return i
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /info", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(collect())
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte(`{"status":"ok"}`)) })
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, r *http.Request) {
		i := collect()
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprintf(w, "# TYPE node_agent_load1 gauge\nnode_agent_load1{node=%q} %f\n", i.Node, i.Load1)
		fmt.Fprintf(w, "# TYPE node_agent_memory_available_mb gauge\nnode_agent_memory_available_mb{node=%q} %d\n", i.Node, i.MemAvailMB)
		fmt.Fprintf(w, "# TYPE node_agent_memory_total_mb gauge\nnode_agent_memory_total_mb{node=%q} %d\n", i.Node, i.MemTotalMB)
	})
	addr := ":" + envOr("PORT", "9100")
	logger.Info("node-agent listening", "addr", addr)
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	if err := srv.ListenAndServe(); err != nil {
		logger.Error("server failed", "error", err.Error())
		os.Exit(1)
	}
}

func envOr(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
