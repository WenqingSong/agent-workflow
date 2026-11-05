package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ledgerd/internal/journal"
	"ledgerd/internal/ledger"
)

type testEnv struct {
	server *httptest.Server
	svc    *ledger.Service
	store  *journal.File
}

func newEnv(t *testing.T) *testEnv {
	t.Helper()
	store, _, err := journal.Open(filepath.Join(t.TempDir(), "ledger.jsonl"))
	if err != nil {
		t.Fatalf("open journal: %v", err)
	}
	svc, err := ledger.Open(store)
	if err != nil {
		t.Fatalf("open ledger: %v", err)
	}
	server := httptest.NewServer(New(svc, nil).Routes())
	env := &testEnv{server: server, svc: svc, store: store}
	t.Cleanup(func() {
		server.Close()
		_ = svc.Close()
	})
	return env
}

func (e *testEnv) request(t *testing.T, method, path string, body any, headers map[string]string) (int, map[string]any) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal body: %v", err)
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, e.server.URL+path, reader)
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for key, value := range headers {
		req.Header.Set(key, value)
	}

	resp, err := e.server.Client().Do(req)
	if err != nil {
		t.Fatalf("do request: %v", err)
	}
	defer resp.Body.Close()

	payload := map[string]any{}
	raw, _ := io.ReadAll(resp.Body)
	if len(bytes.TrimSpace(raw)) > 0 {
		if err := json.Unmarshal(raw, &payload); err != nil {
			t.Fatalf("decode response %s: %v", raw, err)
		}
	}
	return resp.StatusCode, payload
}

func (e *testEnv) createAccount(t *testing.T, name string) string {
	t.Helper()
	status, payload := e.request(t, http.MethodPost, "/accounts", map[string]any{"name": name}, nil)
	if status != http.StatusCreated {
		t.Fatalf("create account: status %d, payload %v", status, payload)
	}
	return payload["id"].(string)
}

func TestFullFlow(t *testing.T) {
	env := newEnv(t)

	status, account := env.request(t, http.MethodPost, "/accounts", map[string]any{"name": "日常"}, nil)
	if status != http.StatusCreated {
		t.Fatalf("create account: %d %v", status, account)
	}
	id := account["id"].(string)
	if account["balance_cents"].(float64) != 0 {
		t.Fatalf("new account balance must be zero: %v", account)
	}

	status, income := env.request(t, http.MethodPost, "/accounts/"+id+"/entries", map[string]any{
		"kind": "income", "amount_cents": 10000, "memo": "工资",
	}, nil)
	if status != http.StatusCreated {
		t.Fatalf("post income: %d %v", status, income)
	}

	status, expense := env.request(t, http.MethodPost, "/accounts/"+id+"/entries", map[string]any{
		"kind": "expense", "amount_cents": 3000, "memo": "午餐",
	}, nil)
	if status != http.StatusCreated {
		t.Fatalf("post expense: %d %v", status, expense)
	}
	if expense["balance_after_cents"].(float64) != 7000 {
		t.Fatalf("unexpected balance after: %v", expense)
	}

	status, balance := env.request(t, http.MethodGet, "/accounts/"+id+"/balance", nil, nil)
	if status != http.StatusOK || balance["balance_cents"].(float64) != 7000 {
		t.Fatalf("balance: %d %v", status, balance)
	}

	status, listing := env.request(t, http.MethodGet, "/accounts/"+id+"/entries?limit=10", nil, nil)
	if status != http.StatusOK {
		t.Fatalf("list entries: %d %v", status, listing)
	}
	items := listing["items"].([]any)
	if len(items) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(items))
	}
	if listing["next_cursor"] != nil {
		t.Fatalf("next_cursor should be null for a short page: %v", listing["next_cursor"])
	}

	status, _ = env.request(t, http.MethodGet, "/healthz", nil, nil)
	if status != http.StatusOK {
		t.Fatalf("healthz: %d", status)
	}
}

func TestErrorSemantics(t *testing.T) {
	env := newEnv(t)
	accountID := env.createAccount(t, "cash")
	if status, _ := env.request(t, http.MethodPost, "/accounts/"+accountID+"/entries", map[string]any{
		"kind": "income", "amount_cents": 100,
	}, nil); status != http.StatusCreated {
		t.Fatalf("seed: %d", status)
	}

	t.Run("invalid body", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodPost, env.server.URL+"/accounts", strings.NewReader("{"))
		req.Header.Set("Content-Type", "application/json")
		resp, err := env.server.Client().Do(req)
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d", resp.StatusCode)
		}
	})

	t.Run("method not allowed", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodDelete, env.server.URL+"/accounts/"+accountID, nil)
		resp, err := env.server.Client().Do(req)
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusMethodNotAllowed {
			t.Fatalf("expected 405, got %d", resp.StatusCode)
		}
		if allow := resp.Header.Get("Allow"); !strings.Contains(allow, http.MethodGet) {
			t.Fatalf("Allow header must list GET, got %q", allow)
		}
	})

	t.Run("head on post route is not allowed", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodHead, env.server.URL+"/accounts", nil)
		resp, err := env.server.Client().Do(req)
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusMethodNotAllowed {
			t.Fatalf("expected 405, got %d", resp.StatusCode)
		}
		if allow := resp.Header.Get("Allow"); strings.Contains(allow, http.MethodHead) {
			t.Fatalf("Allow must not list HEAD for POST-only route, got %q", allow)
		}
	})

	t.Run("unsupported media type", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodPost, env.server.URL+"/accounts", strings.NewReader(`{"name":"x"}`))
		req.Header.Set("Content-Type", "text/plain")
		resp, err := env.server.Client().Do(req)
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusUnsupportedMediaType {
			t.Fatalf("expected 415, got %d", resp.StatusCode)
		}
	})

	t.Run("empty body", func(t *testing.T) {
		req, _ := http.NewRequest(http.MethodPost, env.server.URL+"/accounts", nil)
		req.Header.Set("Content-Type", "application/json")
		resp, err := env.server.Client().Do(req)
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d", resp.StatusCode)
		}
	})

	t.Run("empty idempotency key", func(t *testing.T) {
		status, payload := env.request(t, http.MethodPost, "/accounts/"+accountID+"/entries", map[string]any{
			"kind": "income", "amount_cents": 1,
		}, map[string]string{"Idempotency-Key": ""})
		if status != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d %v", status, payload)
		}
	})

	t.Run("unknown field", func(t *testing.T) {
		status, payload := env.request(t, http.MethodPost, "/accounts", map[string]any{"name": "x", "extra": 1}, nil)
		if status != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d %v", status, payload)
		}
	})

	t.Run("unknown account", func(t *testing.T) {
		status, payload := env.request(t, http.MethodGet, "/accounts/missing/balance", nil, nil)
		if status != http.StatusNotFound {
			t.Fatalf("expected 404, got %d %v", status, payload)
		}
	})

	t.Run("unknown path", func(t *testing.T) {
		status, payload := env.request(t, http.MethodGet, "/nope", nil, nil)
		if status != http.StatusNotFound {
			t.Fatalf("expected 404, got %d %v", status, payload)
		}
	})

	t.Run("insufficient funds", func(t *testing.T) {
		status, payload := env.request(t, http.MethodPost, "/accounts/"+accountID+"/entries", map[string]any{
			"kind": "expense", "amount_cents": 101,
		}, nil)
		if status != http.StatusConflict {
			t.Fatalf("expected 409, got %d %v", status, payload)
		}
		errBody := payload["error"].(map[string]any)
		if errBody["code"] != "insufficient_funds" {
			t.Fatalf("unexpected error code: %v", errBody)
		}
		_, balance := env.request(t, http.MethodGet, "/accounts/"+accountID+"/balance", nil, nil)
		if balance["balance_cents"].(float64) != 100 {
			t.Fatalf("balance must not change: %v", balance)
		}
	})

	t.Run("idempotent replay", func(t *testing.T) {
		headers := map[string]string{"Idempotency-Key": "abc"}
		status, first := env.request(t, http.MethodPost, "/accounts/"+accountID+"/entries", map[string]any{
			"kind": "income", "amount_cents": 500,
		}, headers)
		if status != http.StatusCreated {
			t.Fatalf("first post: %d %v", status, first)
		}
		status, second := env.request(t, http.MethodPost, "/accounts/"+accountID+"/entries", map[string]any{
			"kind": "income", "amount_cents": 500,
		}, headers)
		if status != http.StatusOK {
			t.Fatalf("replay must return 200, got %d %v", status, second)
		}
		if second["entry_id"] != first["entry_id"] {
			t.Fatalf("replay must return the same entry: %v vs %v", second["entry_id"], first["entry_id"])
		}
		if second["replayed"] != true {
			t.Fatalf("replay flag missing: %v", second)
		}
	})

	t.Run("idempotency conflict", func(t *testing.T) {
		headers := map[string]string{"Idempotency-Key": "abc"}
		status, payload := env.request(t, http.MethodPost, "/accounts/"+accountID+"/entries", map[string]any{
			"kind": "income", "amount_cents": 501,
		}, headers)
		if status != http.StatusConflict {
			t.Fatalf("expected 409, got %d %v", status, payload)
		}
		errBody := payload["error"].(map[string]any)
		if errBody["code"] != "idempotency_conflict" {
			t.Fatalf("unexpected code: %v", errBody)
		}
	})

	t.Run("body too large", func(t *testing.T) {
		big := `{"name":"` + strings.Repeat("a", int(MaxBodyBytes)+1024) + `"}`
		req, _ := http.NewRequest(http.MethodPost, env.server.URL+"/accounts", strings.NewReader(big))
		req.Header.Set("Content-Type", "application/json")
		req.ContentLength = int64(len(big))
		resp, err := env.server.Client().Do(req)
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusRequestEntityTooLarge {
			t.Fatalf("expected 413, got %d", resp.StatusCode)
		}
		// 超限请求之后服务仍可正常工作。
		if status, _ := env.request(t, http.MethodGet, "/healthz", nil, nil); status != http.StatusOK {
			t.Fatalf("service must stay healthy after 413, got %d", status)
		}
	})

	t.Run("body too large without content length", func(t *testing.T) {
		// 不声明 Content-Length，走 chunked / 未知长度路径，验证 MaxBytesReader 生效。
		big := `{"name":"` + strings.Repeat("b", int(MaxBodyBytes)+1024) + `"}`
		req, _ := http.NewRequest(http.MethodPost, env.server.URL+"/accounts", readerOnly{strings.NewReader(big)})
		req.Header.Set("Content-Type", "application/json")
		resp, err := env.server.Client().Do(req)
		if err != nil {
			t.Fatalf("request: %v", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusRequestEntityTooLarge {
			t.Fatalf("expected 413, got %d", resp.StatusCode)
		}
		if status, _ := env.request(t, http.MethodGet, "/healthz", nil, nil); status != http.StatusOK {
			t.Fatalf("service must stay healthy after chunked 413, got %d", status)
		}
	})

	t.Run("head is supported on get routes", func(t *testing.T) {
		for _, path := range []string{
			"/healthz",
			"/accounts/" + accountID,
			"/accounts/" + accountID + "/balance",
			"/accounts/" + accountID + "/entries",
		} {
			req, _ := http.NewRequest(http.MethodHead, env.server.URL+path, nil)
			resp, err := env.server.Client().Do(req)
			if err != nil {
				t.Fatalf("request %s: %v", path, err)
			}
			resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("expected 200 for HEAD %s, got %d", path, resp.StatusCode)
			}
		}
	})

	t.Run("invalid query", func(t *testing.T) {
		status, payload := env.request(t, http.MethodGet, "/accounts/"+accountID+"/entries?limit=0", nil, nil)
		if status != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d %v", status, payload)
		}
		status, payload = env.request(t, http.MethodGet, "/accounts/"+accountID+"/entries?after=-1", nil, nil)
		if status != http.StatusBadRequest {
			t.Fatalf("expected 400, got %d %v", status, payload)
		}
	})
}

func TestPaginationCursor(t *testing.T) {
	env := newEnv(t)
	accountID := env.createAccount(t, "cash")
	for index := 0; index < 3; index++ {
		status, payload := env.request(t, http.MethodPost, "/accounts/"+accountID+"/entries", map[string]any{
			"kind": "income", "amount_cents": 10,
		}, nil)
		if status != http.StatusCreated {
			t.Fatalf("post %d: %d %v", index, status, payload)
		}
	}

	status, page := env.request(t, http.MethodGet, "/accounts/"+accountID+"/entries?limit=2", nil, nil)
	if status != http.StatusOK {
		t.Fatalf("page: %d %v", status, page)
	}
	if page["next_cursor"] == nil {
		t.Fatal("expected next_cursor for a full page")
	}
	cursor := int64(page["next_cursor"].(float64))

	status, second := env.request(t, http.MethodGet,
		"/accounts/"+accountID+"/entries?limit=2&after="+itoa(cursor), nil, nil)
	if status != http.StatusOK {
		t.Fatalf("second page: %d %v", status, second)
	}
	if len(second["items"].([]any)) != 1 {
		t.Fatalf("expected 1 remaining entry, got %v", second["items"])
	}
	if second["next_cursor"] != nil {
		t.Fatalf("next_cursor should be null on the last page: %v", second["next_cursor"])
	}
}

func itoa(value int64) string {
	return strconv.FormatInt(value, 10)
}

// readerOnly 隐藏底层 Reader 的具体类型，使 http.Client 不设置 Content-Length。
type readerOnly struct {
	io.Reader
}
