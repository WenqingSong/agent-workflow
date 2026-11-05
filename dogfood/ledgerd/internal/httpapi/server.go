// Package httpapi 提供 ledgerd 的 HTTP 接口。
package httpapi

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"ledgerd/internal/ledger"
)

// MaxBodyBytes 是请求体上限（1 MiB）。
const MaxBodyBytes int64 = 1 << 20

// Handler 实现 ledgerd 的 HTTP 接口。
type Handler struct {
	svc  *ledger.Service
	log  *slog.Logger
	max  int64
	page int
}

// New 构造 HTTP handler。
func New(svc *ledger.Service, logger *slog.Logger) *Handler {
	if logger == nil {
		logger = slog.Default()
	}
	return &Handler{svc: svc, log: logger, max: MaxBodyBytes, page: ledger.DefaultPageSize}
}

// Routes 返回注册好路由的 handler。
func (h *Handler) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /accounts", h.createAccount)
	mux.HandleFunc("GET /accounts/{id}", h.getAccount)
	mux.HandleFunc("GET /accounts/{id}/balance", h.getBalance)
	mux.HandleFunc("POST /accounts/{id}/entries", h.postEntry)
	mux.HandleFunc("GET /accounts/{id}/entries", h.listEntries)
	mux.HandleFunc("GET /healthz", h.health)
	mux.HandleFunc("/", h.notFound)
	return mux
}

type createAccountRequest struct {
	Name string `json:"name"`
}

type postEntryRequest struct {
	Kind        string `json:"kind"`
	AmountCents int64  `json:"amount_cents"`
	Memo        string `json:"memo"`
}

type accountResponse struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	BalanceCents int64     `json:"balance_cents"`
	CreatedAt    time.Time `json:"created_at"`
}

type entryResponse struct {
	EntryID           string    `json:"entry_id"`
	AccountID         string    `json:"account_id"`
	Kind              string    `json:"kind"`
	AmountCents       int64     `json:"amount_cents"`
	BalanceAfterCents int64     `json:"balance_after_cents"`
	Memo              string    `json:"memo,omitempty"`
	CreatedAt         time.Time `json:"created_at"`
	Replayed          bool      `json:"replayed,omitempty"`
}

type listResponse struct {
	Items      []entryResponse `json:"items"`
	NextCursor *int64          `json:"next_cursor"`
}

type errorBody struct {
	Error apiError `json:"error"`
}

type apiError struct {
	Code    string         `json:"code"`
	Message string         `json:"message"`
	Details map[string]any `json:"details,omitempty"`
}

func (h *Handler) createAccount(w http.ResponseWriter, r *http.Request) {
	var req createAccountRequest
	if err := h.decode(w, r, &req); err != nil {
		h.decodeError(w, err)
		return
	}

	account, err := h.svc.CreateAccount(req.Name)
	if err != nil {
		h.serviceError(w, err)
		return
	}
	balance, err := h.svc.Balance(account.ID)
	if err != nil {
		h.serviceError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, accountResponse{
		ID:           account.ID,
		Name:         account.Name,
		BalanceCents: balance,
		CreatedAt:    account.CreatedAt,
	})
}

func (h *Handler) getAccount(w http.ResponseWriter, r *http.Request) {
	account, err := h.svc.Account(r.PathValue("id"))
	if err != nil {
		h.serviceError(w, err)
		return
	}
	balance, err := h.svc.Balance(account.ID)
	if err != nil {
		h.serviceError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, accountResponse{
		ID:           account.ID,
		Name:         account.Name,
		BalanceCents: balance,
		CreatedAt:    account.CreatedAt,
	})
}

func (h *Handler) getBalance(w http.ResponseWriter, r *http.Request) {
	accountID := r.PathValue("id")
	balance, err := h.svc.Balance(accountID)
	if err != nil {
		h.serviceError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"account_id":    accountID,
		"balance_cents": balance,
	})
}

func (h *Handler) postEntry(w http.ResponseWriter, r *http.Request) {
	var req postEntryRequest
	if err := h.decode(w, r, &req); err != nil {
		h.decodeError(w, err)
		return
	}

	entry, replayed, err := h.svc.PostEntry(
		r.PathValue("id"),
		ledger.Kind(strings.ToLower(req.Kind)),
		req.AmountCents,
		req.Memo,
		r.Header.Get("Idempotency-Key"),
	)
	if err != nil {
		h.serviceError(w, err)
		return
	}

	status := http.StatusCreated
	if replayed {
		status = http.StatusOK
	}
	writeJSON(w, status, entryResponse{
		EntryID:           entry.ID,
		AccountID:         entry.AccountID,
		Kind:              string(entry.Kind),
		AmountCents:       entry.AmountCents,
		BalanceAfterCents: entry.BalanceAfterCents,
		Memo:              entry.Memo,
		CreatedAt:         entry.CreatedAt,
		Replayed:          replayed,
	})
}

func (h *Handler) listEntries(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()

	limit := h.page
	if raw := query.Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed <= 0 || parsed > ledger.MaxPageSize {
			writeError(w, http.StatusBadRequest, "invalid_query", "limit must be between 1 and 500", nil)
			return
		}
		limit = parsed
	}

	var after int64
	if raw := query.Get("after"); raw != "" {
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || parsed < 0 {
			writeError(w, http.StatusBadRequest, "invalid_query", "after must be a non-negative integer", nil)
			return
		}
		after = parsed
	}

	entries, err := h.svc.Entries(r.PathValue("id"), after, limit)
	if err != nil {
		h.serviceError(w, err)
		return
	}

	items := make([]entryResponse, 0, len(entries))
	for _, entry := range entries {
		items = append(items, entryResponse{
			EntryID:           entry.ID,
			AccountID:         entry.AccountID,
			Kind:              string(entry.Kind),
			AmountCents:       entry.AmountCents,
			BalanceAfterCents: entry.BalanceAfterCents,
			Memo:              entry.Memo,
			CreatedAt:         entry.CreatedAt,
		})
	}

	var cursor *int64
	if len(entries) == limit {
		last := entries[len(entries)-1].Seq
		cursor = &last
	}
	writeJSON(w, http.StatusOK, listResponse{Items: items, NextCursor: cursor})
}

func (h *Handler) health(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (h *Handler) notFound(w http.ResponseWriter, r *http.Request) {
	writeError(w, http.StatusNotFound, "not_found", "resource not found", nil)
}

func (h *Handler) decode(w http.ResponseWriter, r *http.Request, target any) error {
	r.Body = http.MaxBytesReader(w, r.Body, h.max)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		return errors.New("unexpected trailing data")
	}
	return nil
}

func (h *Handler) decodeError(w http.ResponseWriter, err error) {
	var maxErr *http.MaxBytesError
	if errors.As(err, &maxErr) {
		writeError(w, http.StatusRequestEntityTooLarge, "body_too_large", "request body exceeds 1 MiB", nil)
		return
	}
	writeError(w, http.StatusBadRequest, "invalid_body", "request body is not valid JSON or contains unknown fields", nil)
}

func (h *Handler) serviceError(w http.ResponseWriter, err error) {
	var validation *ledger.ValidationError
	var insufficient *ledger.InsufficientFundsError

	switch {
	case errors.As(err, &validation):
		writeError(w, http.StatusBadRequest, "invalid_"+validation.Field, validation.Reason, nil)
	case errors.As(err, &insufficient):
		writeError(w, http.StatusConflict, "insufficient_funds", "account balance is not enough", map[string]any{
			"balance_cents":  insufficient.Balance,
			"required_cents": insufficient.Required,
		})
	case errors.Is(err, ledger.ErrAccountNotFound):
		writeError(w, http.StatusNotFound, "account_not_found", "account does not exist", nil)
	case errors.Is(err, ledger.ErrIdempotencyConflict):
		writeError(w, http.StatusConflict, "idempotency_conflict", "idempotency key was used with a different payload", nil)
	default:
		h.log.Error("internal error", "error", err)
		writeError(w, http.StatusInternalServerError, "internal_error", "internal server error", nil)
	}
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(payload)
}

func writeError(w http.ResponseWriter, status int, code, message string, details map[string]any) {
	writeJSON(w, status, errorBody{Error: apiError{Code: code, Message: message, Details: details}})
}
