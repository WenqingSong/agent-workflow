package ledger

import (
	"errors"
	"path/filepath"
	"testing"

	"ledgerd/internal/journal"
)

type memStore struct {
	records  []journal.Record
	seq      int64
	failNext bool
}

func (m *memStore) Load() ([]journal.Record, error) {
	out := make([]journal.Record, len(m.records))
	copy(out, m.records)
	return out, nil
}

func (m *memStore) Append(rec journal.Record) (int64, error) {
	if m.failNext {
		m.failNext = false
		return 0, errors.New("injected append failure")
	}
	m.seq++
	rec.Seq = m.seq
	m.records = append(m.records, rec)
	return rec.Seq, nil
}

func (m *memStore) Close() error { return nil }

func newService(t *testing.T) (*Service, *memStore) {
	t.Helper()
	store := &memStore{}
	svc, err := Open(store)
	if err != nil {
		t.Fatalf("open service: %v", err)
	}
	return svc, store
}

func TestCreateAndPostEntries(t *testing.T) {
	svc, _ := newService(t)

	account, err := svc.CreateAccount("  日常  ")
	if err != nil {
		t.Fatalf("create account: %v", err)
	}
	if account.Name != "日常" {
		t.Fatalf("name must be trimmed, got %q", account.Name)
	}

	if _, _, err := svc.PostEntry(account.ID, KindIncome, 10000, "工资", ""); err != nil {
		t.Fatalf("post income: %v", err)
	}
	entry, replayed, err := svc.PostEntry(account.ID, KindExpense, 3000, "午餐", "")
	if err != nil {
		t.Fatalf("post expense: %v", err)
	}
	if replayed {
		t.Fatal("first post must not be a replay")
	}
	if entry.BalanceAfterCents != 7000 {
		t.Fatalf("balance after: got %d, want 7000", entry.BalanceAfterCents)
	}

	balance, err := svc.Balance(account.ID)
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 7000 {
		t.Fatalf("balance: got %d, want 7000", balance)
	}
}

func TestInsufficientFundsDoesNotWrite(t *testing.T) {
	svc, store := newService(t)
	account, _ := svc.CreateAccount("cash")
	if _, _, err := svc.PostEntry(account.ID, KindIncome, 100, "", ""); err != nil {
		t.Fatalf("post income: %v", err)
	}
	before := len(store.records)

	_, _, err := svc.PostEntry(account.ID, KindExpense, 101, "", "")
	if !errors.Is(err, ErrInsufficientFunds) {
		t.Fatalf("expected ErrInsufficientFunds, got %v", err)
	}
	var insufficient *InsufficientFundsError
	if !errors.As(err, &insufficient) {
		t.Fatalf("expected typed error, got %T", err)
	}
	if insufficient.Balance != 100 || insufficient.Required != 101 {
		t.Fatalf("unexpected details: %+v", insufficient)
	}
	if len(store.records) != before {
		t.Fatal("insufficient funds must not append a record")
	}
	balance, _ := svc.Balance(account.ID)
	if balance != 100 {
		t.Fatalf("balance changed: %d", balance)
	}
}

func TestIdempotencyReplayAndConflict(t *testing.T) {
	svc, store := newService(t)
	account, _ := svc.CreateAccount("cash")

	first, replayed, err := svc.PostEntry(account.ID, KindIncome, 500, "bonus", "key-1")
	if err != nil {
		t.Fatalf("first post: %v", err)
	}
	if replayed {
		t.Fatal("first post must not be flagged as replay")
	}
	before := len(store.records)

	second, replayed, err := svc.PostEntry(account.ID, KindIncome, 500, "bonus", "key-1")
	if err != nil {
		t.Fatalf("replay: %v", err)
	}
	if !replayed {
		t.Fatal("second identical post must be a replay")
	}
	if second.ID != first.ID {
		t.Fatalf("replay must return the same entry: %s vs %s", second.ID, first.ID)
	}
	if len(store.records) != before {
		t.Fatal("replay must not append a record")
	}
	balance, _ := svc.Balance(account.ID)
	if balance != 500 {
		t.Fatalf("balance must change once, got %d", balance)
	}

	if _, _, err := svc.PostEntry(account.ID, KindIncome, 501, "bonus", "key-1"); !errors.Is(err, ErrIdempotencyConflict) {
		t.Fatalf("expected idempotency conflict, got %v", err)
	}
	if len(store.records) != before {
		t.Fatal("conflict must not append a record")
	}
}

func TestValidationErrors(t *testing.T) {
	svc, _ := newService(t)
	account, _ := svc.CreateAccount("cash")

	cases := []struct {
		name   string
		kind   Kind
		amount int64
		memo   string
		key    string
	}{
		{"unknown kind", Kind("transfer"), 100, "", ""},
		{"zero amount", KindIncome, 0, "", ""},
		{"negative amount", KindExpense, -5, "", ""},
		{"amount too large", KindIncome, MaxAmountCents + 1, "", ""},
		{"memo too long", KindIncome, 1, string(make([]rune, MaxMemoLength+1)), ""},
		{"idempotency key with spaces", KindIncome, 1, "", " key "},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, _, err := svc.PostEntry(account.ID, testCase.kind, testCase.amount, testCase.memo, testCase.key)
			var validation *ValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("expected ValidationError, got %v", err)
			}
		})
	}

	if _, err := svc.CreateAccount("   "); err == nil {
		t.Fatal("empty name must be rejected")
	}
	if _, _, err := svc.PostEntry("missing", KindIncome, 1, "", ""); !errors.Is(err, ErrAccountNotFound) {
		t.Fatalf("expected account not found, got %v", err)
	}
}

func TestAppendFailureKeepsMemoryConsistent(t *testing.T) {
	svc, store := newService(t)
	account, _ := svc.CreateAccount("cash")
	store.failNext = true

	if _, _, err := svc.PostEntry(account.ID, KindIncome, 100, "", ""); err == nil {
		t.Fatal("append failure must surface")
	}
	balance, _ := svc.Balance(account.ID)
	if balance != 0 {
		t.Fatalf("memory must not change when append fails, got %d", balance)
	}
	entries, _ := svc.Entries(account.ID, 0, 10)
	if len(entries) != 0 {
		t.Fatalf("no entry may be visible when append fails, got %d", len(entries))
	}
}

func TestReplayFromFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")

	file, _, err := journal.Open(path)
	if err != nil {
		t.Fatalf("open journal: %v", err)
	}
	svc, err := Open(file)
	if err != nil {
		t.Fatalf("open service: %v", err)
	}
	account, _ := svc.CreateAccount("cash")
	if _, _, err := svc.PostEntry(account.ID, KindIncome, 2500, "salary", "k1"); err != nil {
		t.Fatalf("post: %v", err)
	}
	if _, _, err := svc.PostEntry(account.ID, KindExpense, 500, "bus", ""); err != nil {
		t.Fatalf("post: %v", err)
	}
	if err := svc.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	reopened, _, err := journal.Open(path)
	if err != nil {
		t.Fatalf("reopen journal: %v", err)
	}
	defer reopened.Close()
	restored, err := Open(reopened)
	if err != nil {
		t.Fatalf("replay: %v", err)
	}

	balance, err := restored.Balance(account.ID)
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 2000 {
		t.Fatalf("balance after replay: got %d, want 2000", balance)
	}
	entries, err := restored.Entries(account.ID, 0, 10)
	if err != nil {
		t.Fatalf("entries: %v", err)
	}
	if len(entries) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(entries))
	}

	// 幂等键必须跨重放生效。
	replayed, isReplay, err := restored.PostEntry(account.ID, KindIncome, 2500, "salary", "k1")
	if err != nil {
		t.Fatalf("replay after restart: %v", err)
	}
	if !isReplay {
		t.Fatal("idempotency key must survive replay")
	}
	if replayed.BalanceAfterCents != 2500 {
		t.Fatalf("unexpected replayed entry: %+v", replayed)
	}
}

func TestEntriesPagination(t *testing.T) {
	svc, _ := newService(t)
	account, _ := svc.CreateAccount("cash")
	for index := 0; index < 5; index++ {
		if _, _, err := svc.PostEntry(account.ID, KindIncome, int64(index+1), "", ""); err != nil {
			t.Fatalf("post %d: %v", index, err)
		}
	}

	first, err := svc.Entries(account.ID, 0, 2)
	if err != nil {
		t.Fatalf("entries: %v", err)
	}
	if len(first) != 2 || first[0].AmountCents != 1 || first[1].AmountCents != 2 {
		t.Fatalf("unexpected first page: %+v", first)
	}

	second, err := svc.Entries(account.ID, first[1].Seq, 2)
	if err != nil {
		t.Fatalf("entries: %v", err)
	}
	if len(second) != 2 || second[0].AmountCents != 3 {
		t.Fatalf("unexpected second page: %+v", second)
	}

	if _, err := svc.Entries("missing", 0, 2); !errors.Is(err, ErrAccountNotFound) {
		t.Fatalf("expected account not found, got %v", err)
	}
}
