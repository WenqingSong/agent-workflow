// Package httpapi 提供 ledgerd 的 HTTP 接口。
package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
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
//
// 已知路径额外注册无方法前缀的兜底，使「路径存在但方法不支持」返回 405 而不是 404。
func (h *Handler) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /accounts", h.createAccount)
	mux.HandleFunc("/accounts", h.methodNotAllowed(http.MethodPost))
	mux.HandleFunc("GET /accounts/{id}", h.getAccount)
	mux.HandleFunc("/accounts/{id}", h.methodNotAllowed(http.MethodGet))
	mux.HandleFunc("GET /accounts/{id}/balance", h.getBalance)
	mux.HandleFunc("/accounts/{id}/balance", h.methodNotAllowed(http.MethodGet))
	mux.HandleFunc("POST /accounts/{id}/entries", h.postEntry)
	mux.HandleFunc("GET /accounts/{id}/entries", h.listEntries)
	mux.HandleFunc("/accounts/{id}/entries", h.methodNotAllowed(http.MethodGet, http.MethodPost))
	mux.HandleFunc("GET /healthz", h.health)
	mux.HandleFunc("/healthz", h.methodNotAllowed(http.MethodGet))
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
	h.writeJSON(w, http.StatusCreated, accountResponse{
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
	h.writeJSON(w, http.StatusOK, accountResponse{
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
	h.writeJSON(w, http.StatusOK, map[string]any{
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

	// 区分「未提供幂等键」与「提供了空键」：后者是非法输入。
	idempotencyKey := ""
	if values, present := r.Header["Idempotency-Key"]; present {
		if len(values) != 1 || strings.TrimSpace(values[0]) == "" {
			h.writeError(w, http.StatusBadRequest, "invalid_idempotency_key", "Idempotency-Key must be a single non-empty value", nil)
			return
		}
		idempotencyKey = values[0]
	}

	entry, replayed, err := h.svc.PostEntry(
		r.PathValue("id"),
		ledger.Kind(strings.ToLower(req.Kind)),
		req.AmountCents,
		req.Memo,
		idempotencyKey,
	)
	if err != nil {
		h.serviceError(w, err)
		return
	}

	status := http.StatusCreated
	if replayed {
		status = http.StatusOK
	}
	h.writeJSON(w, status, entryResponse{
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
			h.writeError(w, http.StatusBadRequest, "invalid_query", fmt.Sprintf("limit must be between 1 and %d", ledger.MaxPageSize), nil)
			return
		}
		limit = parsed
	}

	var after int64
	if raw := query.Get("after"); raw != "" {
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || parsed < 0 {
			h.writeError(w, http.StatusBadRequest, "invalid_query", "after must be a non-negative integer", nil)
			return
		}
		after = parsed
	}

	// 多取一条以判断是否真的有下一页，避免满页但已是最后一页时仍返回游标。
	entries, err := h.svc.Entries(r.PathValue("id"), after, limit+1)
	if err != nil {
		h.serviceError(w, err)
		return
	}
	hasMore := len(entries) > limit
	if hasMore {
		entries = entries[:limit]
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
	if hasMore && len(entries) > 0 {
		last := entries[len(entries)-1].Seq
		cursor = &last
	}
	h.writeJSON(w, http.StatusOK, listResponse{Items: items, NextCursor: cursor})
}

func (h *Handler) health(w http.ResponseWriter, r *http.Request) {
	h.writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (h *Handler) notFound(w http.ResponseWriter, r *http.Request) {
	h.writeError(w, http.StatusNotFound, "not_found", "resource not found", nil)
}

func (h *Handler) methodNotAllowed(allowed ...string) http.HandlerFunc {
	methods := append([]string{}, allowed...)
	for _, method := range methods {
		if method == http.MethodGet {
			methods = append(methods, http.MethodHead)
			break
		}
	}
	allow := strings.Join(methods, ", ")
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Allow", allow)
		h.writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", "method not allowed", nil)
	}
}

// logger 返回可用的 logger，避免在请求路径上写共享字段。
func (h *Handler) logger() *slog.Logger {
	if h.log != nil {
		return h.log
	}
	return slog.Default()
}

var (
	errEmptyBody            = errors.New("empty body")
	errUnsupportedMediaType = errors.New("unsupported media type")
)

func (h *Handler) decode(w http.ResponseWriter, r *http.Request, target any) error {
	if r.ContentLength > h.max {
		return &http.MaxBytesError{Limit: h.max}
	}
	if r.ContentLength == 0 {
		return errEmptyBody
	}
	contentType := strings.ToLower(strings.TrimSpace(r.Header.Get("Content-Type")))
	if !strings.HasPrefix(contentType, "application/json") {
		return errUnsupportedMediaType
	}

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
	switch {
	case errors.As(err, &maxErr):
		h.writeError(w, http.StatusRequestEntityTooLarge, "body_too_large", fmt.Sprintf("request body exceeds %d bytes", h.max), nil)
	case errors.Is(err, errEmptyBody):
		h.writeError(w, http.StatusBadRequest, "invalid_body", "request body is required", nil)
	case errors.Is(err, errUnsupportedMediaType):
		h.writeError(w, http.StatusUnsupportedMediaType, "unsupported_media_type", "Content-Type must be application/json", nil)
	default:
		h.writeError(w, http.StatusBadRequest, "invalid_body", "request body is not valid JSON or contains unknown fields", nil)
	}
}

func (h *Handler) serviceError(w http.ResponseWriter, err error) {
	var validation *ledger.ValidationError
	var insufficient *ledger.InsufficientFundsError

	switch {
	case errors.As(err, &validation):
		code := "invalid_" + strings.ToLower(strings.ReplaceAll(validation.Field, "-", "_"))
		h.writeError(w, http.StatusBadRequest, code, validation.Reason, nil)
	case errors.As(err, &insufficient):
		h.writeError(w, http.StatusConflict, "insufficient_funds", "account balance is not enough", map[string]any{
			"balance_cents":  insufficient.Balance,
			"required_cents": insufficient.Required,
		})
	case errors.Is(err, ledger.ErrAccountNotFound):
		h.writeError(w, http.StatusNotFound, "account_not_found", "account does not exist", nil)
	case errors.Is(err, ledger.ErrIdempotencyConflict):
		h.writeError(w, http.StatusConflict, "idempotency_conflict", "idempotency key was used with a different payload", nil)
	default:
		h.logger().Error("internal error", "error", err)
		h.writeError(w, http.StatusInternalServerError, "internal_error", "internal server error", nil)
	}
}

func (h *Handler) writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(payload); err != nil {
		// Header 已发出，无法再改状态码；至少留下可观测记录。
		h.logger().Error("encode response failed", "error", err)
	}
}

func (h *Handler) writeError(w http.ResponseWriter, status int, code, message string, details map[string]any) {
	h.writeJSON(w, status, errorBody{Error: apiError{Code: code, Message: message, Details: details}})
}
