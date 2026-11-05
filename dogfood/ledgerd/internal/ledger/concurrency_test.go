package ledger

import (
	"errors"
	"sync"
	"sync/atomic"
	"testing"
)

// TestConcurrentExpensesNeverOverdraw 验证并发支出不会超支。
//
// -race 只能发现数据竞争，发现不了「检查后写入」的逻辑竞态；
// 因此这里断言余额不变量与成功/冲突笔数，而不是只依赖 race 检测。
func TestConcurrentExpensesNeverOverdraw(t *testing.T) {
	svc, store := newService(t)
	account, err := svc.CreateAccount("cash")
	if err != nil {
		t.Fatalf("create account: %v", err)
	}
	if _, _, err := svc.PostEntry(account.ID, KindIncome, 100, "seed", ""); err != nil {
		t.Fatalf("seed: %v", err)
	}

	const (
		workers = 50
		amount  = int64(10)
	)
	var success, conflicts, unexpected int64
	var wg sync.WaitGroup
	start := make(chan struct{})

	for index := 0; index < workers; index++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			_, _, err := svc.PostEntry(account.ID, KindExpense, amount, "concurrent", "")
			switch {
			case err == nil:
				atomic.AddInt64(&success, 1)
			case errors.Is(err, ErrInsufficientFunds):
				atomic.AddInt64(&conflicts, 1)
			default:
				atomic.AddInt64(&unexpected, 1)
			}
		}()
	}
	close(start)
	wg.Wait()

	if unexpected != 0 {
		t.Fatalf("unexpected errors: %d", unexpected)
	}
	if success != 10 {
		t.Fatalf("exactly 10 expenses must succeed, got %d", success)
	}
	if conflicts != workers-10 {
		t.Fatalf("expected %d insufficient-funds errors, got %d", workers-10, conflicts)
	}

	balance, err := svc.Balance(account.ID)
	if err != nil {
		t.Fatalf("balance: %v", err)
	}
	if balance != 0 {
		t.Fatalf("balance must be zero, got %d", balance)
	}
	if success*amount+balance != 100 {
		t.Fatalf("invariant broken: success(%d)*amount(%d)+balance(%d) != 100", success, amount, balance)
	}

	// 记录数 = 1 个账户 + 1 笔种子收入 + 10 笔成功支出。
	if int64(len(store.records)) != 12 {
		t.Fatalf("unexpected record count: %d", len(store.records))
	}
}

// TestConcurrentIdempotentPosts 验证同一幂等键的并发请求只记一笔账。
func TestConcurrentIdempotentPosts(t *testing.T) {
	svc, store := newService(t)
	account, _ := svc.CreateAccount("cash")

	const workers = 24
	var wg sync.WaitGroup
	var failures int64
	ids := make([]string, workers)
	start := make(chan struct{})

	for index := 0; index < workers; index++ {
		wg.Add(1)
		go func(slot int) {
			defer wg.Done()
			<-start
			entry, _, err := svc.PostEntry(account.ID, KindIncome, 500, "bonus", "same-key")
			if err != nil {
				atomic.AddInt64(&failures, 1)
				return
			}
			ids[slot] = entry.ID
		}(index)
	}
	close(start)
	wg.Wait()

	if failures != 0 {
		t.Fatalf("idempotent posts must not fail: %d", failures)
	}
	for index, id := range ids {
		if id != ids[0] {
			t.Fatalf("all replays must return the same entry: slot %d got %s, want %s", index, id, ids[0])
		}
	}

	balance, _ := svc.Balance(account.ID)
	if balance != 500 {
		t.Fatalf("balance must change exactly once, got %d", balance)
	}
	// 1 个账户记录 + 1 笔分录。
	if len(store.records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(store.records))
	}
}
