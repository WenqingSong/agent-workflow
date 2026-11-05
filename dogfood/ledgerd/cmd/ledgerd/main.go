// Command ledgerd 是个人账本 HTTP 服务。
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"ledgerd/internal/httpapi"
	"ledgerd/internal/journal"
	"ledgerd/internal/ledger"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "ledgerd: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	flags := flag.NewFlagSet("ledgerd", flag.ContinueOnError)
	addr := flags.String("addr", "127.0.0.1:8080", "HTTP listen address")
	data := flags.String("data", "ledger.jsonl", "journal file path")
	shutdownTimeout := flags.Duration("shutdown-timeout", 5*time.Second, "graceful shutdown timeout")
	if err := flags.Parse(args); err != nil {
		return err
	}

	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))

	store, warnings, err := journal.Open(*data)
	if err != nil {
		return err
	}
	for _, warning := range warnings {
		switch warning.Kind {
		case journal.WarningRepairedNewline:
			logger.Warn("journal repaired missing trailing newline",
				"line", warning.Line, "reason", warning.Reason)
		default:
			logger.Warn("journal recovered from torn tail",
				"line", warning.Line, "reason", warning.Reason, "truncated_bytes", warning.Bytes)
		}
	}

	service, err := ledger.Open(store)
	if err != nil {
		_ = store.Close()
		return err
	}
	defer service.Close()

	listener, err := net.Listen("tcp", *addr)
	if err != nil {
		return err
	}

	handler := httpapi.New(service, logger)
	server := &http.Server{
		Handler:           handler.Routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
		ErrorLog:          slog.NewLogLogger(logger.Handler(), slog.LevelError),
	}

	// 供集成测试与运维脚本发现实际监听地址。
	fmt.Printf("LEDGERD_ADDR=%s\n", listener.Addr().String())
	os.Stdout.Sync()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() {
		if serveErr := server.Serve(listener); serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
			errCh <- serveErr
			return
		}
		errCh <- nil
	}()

	select {
	case err := <-errCh:
		if err != nil {
			return err
		}
		return nil
	case <-ctx.Done():
		logger.Info("shutdown signal received", "timeout", shutdownTimeout.String())
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), *shutdownTimeout)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		_ = server.Close()
		return fmt.Errorf("graceful shutdown failed: %w", err)
	}
	if serveErr := <-errCh; serveErr != nil {
		return serveErr
	}
	logger.Info("server stopped")
	return nil
}
