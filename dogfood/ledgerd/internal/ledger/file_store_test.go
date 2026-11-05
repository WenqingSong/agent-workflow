package ledger

import (
	"path/filepath"
	"sync"
	"testing"

	"ledgerd/internal/journal"
)

// TestConcurrentPostsWithRealJournal 用真实 journal.File 跑并发写入，
// 覆盖 memStore 绕过的落盘与互斥路径，并在重放后校验余额不变量。
func TestConcurrentPostsWithRealJournal(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")

	file, _, err := journal.Open(path)
	if err != nil {
		t.Fatalf("open journal: %v", err)
	}
	svc, err := Open(file)
	if err != nil {
		t.Fatalf("open service: %v", err)
	}
	account, err := svc.CreateAccount("cash")
	if err != nil {
		t.Fatalf("create account: %v", err)
	}

	const workers = 20
	var wg sync.WaitGroup
	errs := make([]error, workers)
	start := make(chan struct{})
	for index := 0; index < workers; index++ {
		wg.Add(1)
		go func(slot int) {
			defer wg.Done()
			<-start
			_, _, errs[slot] = svc.PostEntry(account.ID, KindIncome, 5, "concurrent", "")
		}(index)
	}
	close(start)
	wg.Wait()
	for index, err := range errs {
		if err != nil {
			t.Fatalf("worker %d failed: %v", index, err)
		}
	}

	if err := svc.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	reopened, _, err := journal.Open(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
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
	if balance != workers*5 {
		t.Fatalf("balance after replay: got %d, want %d", balance, workers*5)
	}
	entries, err := restored.Entries(account.ID, 0, workers+1)
	if err != nil {
		t.Fatalf("entries: %v", err)
	}
	if len(entries) != workers {
		t.Fatalf("entry count after replay: got %d, want %d", len(entries), workers)
	}
}
