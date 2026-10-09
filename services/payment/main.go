// Payment service: a fake payment gateway with tunable failure modes.
//
// Behaviour comes from env vars (defaults) and is overridden by a JSON file
// (CHAOS_CONFIG_FILE, mounted from a ConfigMap). The file is re-read every few
// seconds, so editing the ConfigMap changes behaviour WITHOUT a pod restart --
// unlike env vars, which are only read at container start. See docs/labs/04-configmap-hot-reload.md.
//
// Standard library only, so no go.sum and a tiny distroless image.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	mrand "math/rand/v2"
	"net/http"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

type chaosConfig struct {
	FailureRate float64 `json:"failure_rate"` // fraction of requests answered with 503
	DeclineRate float64 `json:"decline_rate"` // fraction of cards "declined" (402)
	LatencyMs   int     `json:"latency_ms"`   // base latency per charge
	JitterMs    int     `json:"jitter_ms"`    // random extra latency 0..jitter
	HangRate    float64 `json:"hang_rate"`    // fraction of requests that hang 30s (timeouts!)
}

var (
	service = "payment"
	version = envOr("APP_VERSION", "dev")
	pod     = envOr("POD_NAME", hostname())
	logger  = slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			// Cloud Logging expects "severity" and "message".
			switch a.Key {
			case slog.LevelKey:
				a.Key = "severity"
				if a.Value.String() == "WARN" {
					a.Value = slog.StringValue("WARNING")
				}
			case slog.MessageKey:
				a.Key = "message"
			}
			return a
		},
	})).With("service", service, "pod", pod, "version", version)

	cfgMu sync.RWMutex
	cfg   chaosConfig

	ready atomic.Bool

	idemMu sync.Mutex
	idem   = map[string]chargeResponse{}

	metrics = newMetrics()
)

type chargeRequest struct {
	OrderID     string `json:"order_id"`
	AmountCents int    `json:"amount_cents"`
}

type chargeResponse struct {
	PaymentID   string `json:"payment_id"`
	OrderID     string `json:"order_id"`
	AmountCents int    `json:"amount_cents"`
	Status      string `json:"status"`
}

func main() {
	cfg = chaosConfig{
		FailureRate: envFloat("PAYMENT_FAILURE_RATE", 0.0),
		DeclineRate: envFloat("PAYMENT_DECLINE_RATE", 0.03),
		LatencyMs:   int(envFloat("PAYMENT_LATENCY_MS", 80)),
		JitterMs:    int(envFloat("PAYMENT_JITTER_MS", 120)),
	}
	defaults := cfg
	if f := os.Getenv("CHAOS_CONFIG_FILE"); f != "" {
		go watchConfig(f, defaults)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("POST /charge", handleCharge)
	mux.HandleFunc("GET /admin/chaos", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, currentConfig())
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		if !ready.Load() {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "shutting down"})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})
	mux.HandleFunc("GET /metrics", metrics.serve)

	srv := &http.Server{
		Addr:              ":" + envOr("PORT", "8080"),
		Handler:           withHeaders(mux),
		ReadHeaderTimeout: 5 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go func() {
		logger.Info("payment listening", "addr", srv.Addr, "config", currentConfig())
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("server failed", "error", err.Error())
			os.Exit(1)
		}
	}()
	ready.Store(true)

	<-ctx.Done()
	// Graceful shutdown: stop advertising readiness, then finish in-flight requests.
	logger.Info("SIGTERM received, draining")
	ready.Store(false)
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		logger.Error("forced shutdown", "error", err.Error())
	}
	logger.Info("bye")
}

func handleCharge(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	outcome := "ok"
	defer func() { metrics.observe(outcome, time.Since(start)) }()

	var req chargeRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&req); err != nil || req.OrderID == "" || req.AmountCents <= 0 {
		outcome = "bad_request"
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "order_id and positive amount_cents required"})
		return
	}

	// Idempotency: a retried request with the same key gets the same answer, never a double charge.
	key := r.Header.Get("Idempotency-Key")
	if key != "" {
		idemMu.Lock()
		prev, ok := idem[key]
		idemMu.Unlock()
		if ok {
			outcome = "idempotent_replay"
			writeJSON(w, http.StatusOK, prev)
			return
		}
	}

	c := currentConfig()
	delay := time.Duration(c.LatencyMs) * time.Millisecond
	if c.JitterMs > 0 {
		delay += time.Duration(mrand.IntN(c.JitterMs)) * time.Millisecond
	}
	if c.HangRate > 0 && mrand.Float64() < c.HangRate {
		delay = 30 * time.Second
		outcome = "hang"
	}
	select {
	case <-time.After(delay):
	case <-r.Context().Done(): // caller gave up (timeout) -- stop working too
		outcome = "client_cancelled"
		return
	}

	if mrand.Float64() < c.FailureRate {
		outcome = "error"
		logger.Warn("simulated gateway failure", "order_id", req.OrderID)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "upstream gateway unavailable"})
		return
	}
	if mrand.Float64() < c.DeclineRate {
		outcome = "declined"
		writeJSON(w, http.StatusPaymentRequired, map[string]string{"error": "card declined"})
		return
	}

	resp := chargeResponse{PaymentID: "pay_" + randomID(), OrderID: req.OrderID, AmountCents: req.AmountCents, Status: "captured"}
	if key != "" {
		idemMu.Lock()
		if len(idem) > 50000 { // crude bound so memory doesn't grow forever
			idem = map[string]chargeResponse{}
		}
		idem[key] = resp
		idemMu.Unlock()
	}
	if outcome == "hang" {
		outcome = "ok_slow"
	}
	writeJSON(w, http.StatusOK, resp)
}

// watchConfig polls the mounted ConfigMap file. Kubernetes updates the file via an
// atomic symlink swap, typically within ~60s of `kubectl apply` on the ConfigMap.
func watchConfig(path string, defaults chaosConfig) {
	var last string
	for {
		data, err := os.ReadFile(path)
		if err == nil && string(data) != last {
			next := defaults
			if err := json.Unmarshal(data, &next); err != nil {
				logger.Error("invalid chaos config, keeping previous", "error", err.Error())
			} else {
				cfgMu.Lock()
				cfg = next
				cfgMu.Unlock()
				logger.Warn("chaos config reloaded", "config", next)
			}
			last = string(data)
		}
		time.Sleep(5 * time.Second)
	}
}

func currentConfig() chaosConfig {
	cfgMu.RLock()
	defer cfgMu.RUnlock()
	return cfg
}

func withHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Served-By", pod)
		w.Header().Set("X-Service", service+"@"+version)
		next.ServeHTTP(w, r)
	})
}

// ---------- minimal Prometheus exposition (no client library needed) ----------
type promMetrics struct {
	mu      sync.Mutex
	counts  map[string]uint64
	buckets []float64
	bucketN []uint64
	sum     float64
	n       uint64
}

func newMetrics() *promMetrics {
	b := []float64{0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30}
	return &promMetrics{counts: map[string]uint64{}, buckets: b, bucketN: make([]uint64, len(b))}
}

func (m *promMetrics) observe(outcome string, d time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.counts[outcome]++
	s := d.Seconds()
	m.sum += s
	m.n++
	for i, le := range m.buckets {
		if s <= le {
			m.bucketN[i]++
		}
	}
}

func (m *promMetrics) serve(w http.ResponseWriter, _ *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var sb strings.Builder
	sb.WriteString("# HELP payment_charges_total Charge requests by outcome\n# TYPE payment_charges_total counter\n")
	keys := make([]string, 0, len(m.counts))
	for k := range m.counts {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintf(&sb, "payment_charges_total{outcome=%q} %d\n", k, m.counts[k])
	}
	sb.WriteString("# HELP payment_charge_duration_seconds Charge latency\n# TYPE payment_charge_duration_seconds histogram\n")
	for i, le := range m.buckets {
		fmt.Fprintf(&sb, "payment_charge_duration_seconds_bucket{le=\"%s\"} %d\n", strconv.FormatFloat(le, 'f', -1, 64), m.bucketN[i])
	}
	fmt.Fprintf(&sb, "payment_charge_duration_seconds_bucket{le=\"+Inf\"} %d\n", m.n)
	fmt.Fprintf(&sb, "payment_charge_duration_seconds_sum %f\npayment_charge_duration_seconds_count %d\n", m.sum, m.n)
	c := currentConfig()
	sb.WriteString("# HELP payment_chaos_failure_rate Configured failure rate\n# TYPE payment_chaos_failure_rate gauge\n")
	fmt.Fprintf(&sb, "payment_chaos_failure_rate %f\n", c.FailureRate)
	sb.WriteString("# HELP payment_chaos_latency_ms Configured base latency\n# TYPE payment_chaos_latency_ms gauge\n")
	fmt.Fprintf(&sb, "payment_chaos_latency_ms %d\n", c.LatencyMs)
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	_, _ = w.Write([]byte(sb.String()))
}

// ---------- helpers ----------
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(k), 64); err == nil {
		return v
	}
	return def
}

func hostname() string {
	h, _ := os.Hostname()
	return h
}

func randomID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
