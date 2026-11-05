// Package ledger 实现个人账本的领域逻辑：账户、分录、余额、幂等与校验。
//
// 持久化通过 Store 完成。只有在事件成功落盘后才更新内存状态，
// 因此写盘失败不会留下「内存已扣款、磁盘没有」的分叉。
package ledger

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"sync"
	"time"

	"ledgerd/internal/journal"
)

// Kind 是分录方向。
type Kind string

const (
	KindIncome  Kind = "income"
	KindExpense Kind = "expense"
)

// 输入边界。
const (
	MaxNameLength              = 100
	MaxMemoLength              = 200
	MaxIdempotencyKeyLen       = 128
	MaxAmountCents       int64 = 100_000_000_000 // 1e11 分
	DefaultPageSize            = 50
	MaxPageSize                = 500
)

// 领域错误。
var (
	ErrAccountNotFound     = errors.New("account not found")
	ErrInsufficientFunds   = errors.New("insufficient funds")
	ErrIdempotencyConflict = errors.New("idempotency key conflict")
)

// ValidationError 表示输入不合法。
type ValidationError struct {
	Field  string
	Reason string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("%s: %s", e.Field, e.Reason)
}

// InsufficientFundsError 携带余额与所需金额。
type InsufficientFundsError struct {
	Balance  int64
	Required int64
}

func (e *InsufficientFundsError) Error() string {
	return fmt.Sprintf("insufficient funds: balance=%d required=%d", e.Balance, e.Required)
}

func (e *InsufficientFundsError) Is(target error) bool { return target == ErrInsufficientFunds }

// Account 是账本账户。
type Account struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	CreatedAt time.Time `json:"created_at"`
}

// Entry 是一条已记账的分录。
type Entry struct {
	ID                string    `json:"entry_id"`
	AccountID         string    `json:"account_id"`
	Kind              Kind      `json:"kind"`
	AmountCents       int64     `json:"amount_cents"`
	BalanceAfterCents int64     `json:"balance_after_cents"`
	Memo              string    `json:"memo,omitempty"`
	CreatedAt         time.Time `json:"created_at"`
	Seq               int64     `json:"-"`
}

// Store 是持久化边界。
type Store interface {
	Load() ([]journal.Record, error)
	Append(journal.Record) (int64, error)
	Close() error
}

type accountRecord struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	CreatedAt time.Time `json:"created_at"`
}

type entryRecord struct {
	ID                string    `json:"id"`
	AccountID         string    `json:"account_id"`
	Kind              string    `json:"kind"`
	AmountCents       int64     `json:"amount_cents"`
	BalanceAfterCents int64     `json:"balance_after_cents"`
	Memo              string    `json:"memo,omitempty"`
	IdempotencyKey    string    `json:"idempotency_key,omitempty"`
	CreatedAt         time.Time `json:"created_at"`
}

const (
	recordAccountCreated = "account_created"
	recordEntryPosted    = "entry_posted"
)

type idemRecord struct {
	entryID     string
	payloadHash string
}

// Service 是账本应用服务，单进程并发安全。
type Service struct {
	mu       sync.Mutex
	store    Store
	accounts map[string]Account
	entries  map[string][]Entry
	balances map[string]int64
	idem     map[string]idemRecord
}

// Open 从 Store 加载并重放全部事件。
func Open(store Store) (*Service, error) {
	records, err := store.Load()
	if err != nil {
		return nil, err
	}

	svc := &Service{
		store:    store,
		accounts: make(map[string]Account),
		entries:  make(map[string][]Entry),
		balances: make(map[string]int64),
		idem:     make(map[string]idemRecord),
	}

	for _, rec := range records {
		switch rec.Type {
		case recordAccountCreated:
			var data accountRecord
			if err := json.Unmarshal(rec.Data, &data); err != nil {
				return nil, fmt.Errorf("ledger: replay account at seq %d: %w", rec.Seq, err)
			}
			if data.ID == "" {
				return nil, fmt.Errorf("ledger: replay account at seq %d: empty id", rec.Seq)
			}
			if _, exists := svc.accounts[data.ID]; exists {
				return nil, fmt.Errorf("ledger: replay account at seq %d: duplicate id %s", rec.Seq, data.ID)
			}
			svc.accounts[data.ID] = Account{ID: data.ID, Name: data.Name, CreatedAt: data.CreatedAt}
			svc.balances[data.ID] = 0

		case recordEntryPosted:
			var data entryRecord
			if err := json.Unmarshal(rec.Data, &data); err != nil {
				return nil, fmt.Errorf("ledger: replay entry at seq %d: %w", rec.Seq, err)
			}
			if _, exists := svc.accounts[data.AccountID]; !exists {
				return nil, fmt.Errorf("ledger: replay entry at seq %d: unknown account %s", rec.Seq, data.AccountID)
			}
			balance, ok := applyAmount(svc.balances[data.AccountID], Kind(data.Kind), data.AmountCents)
			if !ok {
				return nil, fmt.Errorf("ledger: replay entry at seq %d: invalid amount or overflow", rec.Seq)
			}
			if balance != data.BalanceAfterCents {
				return nil, fmt.Errorf(
					"ledger: replay entry at seq %d: balance mismatch: computed %d, recorded %d",
					rec.Seq, balance, data.BalanceAfterCents,
				)
			}
			entry := Entry{
				ID:                data.ID,
				AccountID:         data.AccountID,
				Kind:              Kind(data.Kind),
				AmountCents:       data.AmountCents,
				BalanceAfterCents: data.BalanceAfterCents,
				Memo:              data.Memo,
				CreatedAt:         data.CreatedAt,
				Seq:               rec.Seq,
			}
			svc.entries[data.AccountID] = append(svc.entries[data.AccountID], entry)
			svc.balances[data.AccountID] = balance
			if data.IdempotencyKey != "" {
				key := idemKey(data.AccountID, data.IdempotencyKey)
				if _, exists := svc.idem[key]; exists {
					return nil, fmt.Errorf("ledger: replay entry at seq %d: duplicate idempotency key", rec.Seq)
				}
				svc.idem[key] = idemRecord{
					entryID:     data.ID,
					payloadHash: hashPayload(Kind(data.Kind), data.AmountCents, data.Memo),
				}
			}

		default:
			return nil, fmt.Errorf("ledger: replay seq %d: unknown record type %q", rec.Seq, rec.Type)
		}
	}

	return svc, nil
}

// CreateAccount 创建账户并持久化。
func (s *Service) CreateAccount(name string) (Account, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return Account{}, &ValidationError{Field: "name", Reason: "must not be empty"}
	}
	if len([]rune(name)) > MaxNameLength {
		return Account{}, &ValidationError{Field: "name", Reason: fmt.Sprintf("must be at most %d characters", MaxNameLength)}
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	account := Account{
		ID:        newID("acc"),
		Name:      name,
		CreatedAt: time.Now().UTC(),
	}
	data, err := json.Marshal(accountRecord{ID: account.ID, Name: account.Name, CreatedAt: account.CreatedAt})
	if err != nil {
		return Account{}, fmt.Errorf("ledger: marshal account: %w", err)
	}
	if _, err := s.store.Append(journal.Record{Type: recordAccountCreated, Data: data}); err != nil {
		return Account{}, fmt.Errorf("ledger: persist account: %w", err)
	}

	s.accounts[account.ID] = account
	s.balances[account.ID] = 0
	return account, nil
}

// PostEntry 记一笔分录。replayed 为 true 表示幂等命中，未新增记录。
func (s *Service) PostEntry(accountID string, kind Kind, amountCents int64, memo, idempotencyKey string) (Entry, bool, error) {
	if kind != KindIncome && kind != KindExpense {
		return Entry{}, false, &ValidationError{Field: "kind", Reason: `must be "income" or "expense"`}
	}
	if amountCents <= 0 {
		return Entry{}, false, &ValidationError{Field: "amount_cents", Reason: "must be positive"}
	}
	if amountCents > MaxAmountCents {
		return Entry{}, false, &ValidationError{
			Field:  "amount_cents",
			Reason: fmt.Sprintf("must be at most %d", MaxAmountCents),
		}
	}
	if len([]rune(memo)) > MaxMemoLength {
		return Entry{}, false, &ValidationError{
			Field:  "memo",
			Reason: fmt.Sprintf("must be at most %d characters", MaxMemoLength),
		}
	}
	if strings.TrimSpace(idempotencyKey) != idempotencyKey || len([]rune(idempotencyKey)) > MaxIdempotencyKeyLen {
		return Entry{}, false, &ValidationError{
			Field:  "Idempotency-Key",
			Reason: fmt.Sprintf("must be trimmed and at most %d characters", MaxIdempotencyKeyLen),
		}
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if _, ok := s.accounts[accountID]; !ok {
		return Entry{}, false, ErrAccountNotFound
	}

	payloadHash := hashPayload(kind, amountCents, memo)
	if idempotencyKey != "" {
		key := idemKey(accountID, idempotencyKey)
		if existing, ok := s.idem[key]; ok {
			if existing.payloadHash != payloadHash {
				return Entry{}, false, ErrIdempotencyConflict
			}
			for _, entry := range s.entries[accountID] {
				if entry.ID == existing.entryID {
					return entry, true, nil
				}
			}
			return Entry{}, false, fmt.Errorf("ledger: idempotency index points to missing entry %s", existing.entryID)
		}
	}

	balance := s.balances[accountID]
	next, ok := applyAmount(balance, kind, amountCents)
	if !ok {
		if kind == KindExpense {
			return Entry{}, false, &InsufficientFundsError{Balance: balance, Required: amountCents}
		}
		return Entry{}, false, &ValidationError{Field: "amount_cents", Reason: "balance overflow"}
	}

	entry := Entry{
		ID:                newID("ent"),
		AccountID:         accountID,
		Kind:              kind,
		AmountCents:       amountCents,
		BalanceAfterCents: next,
		Memo:              memo,
		CreatedAt:         time.Now().UTC(),
	}
	record := entryRecord{
		ID:                entry.ID,
		AccountID:         entry.AccountID,
		Kind:              string(entry.Kind),
		AmountCents:       entry.AmountCents,
		BalanceAfterCents: entry.BalanceAfterCents,
		Memo:              entry.Memo,
		IdempotencyKey:    idempotencyKey,
		CreatedAt:         entry.CreatedAt,
	}
	data, err := json.Marshal(record)
	if err != nil {
		return Entry{}, false, fmt.Errorf("ledger: marshal entry: %w", err)
	}
	seq, err := s.store.Append(journal.Record{Type: recordEntryPosted, Data: data})
	if err != nil {
		return Entry{}, false, fmt.Errorf("ledger: persist entry: %w", err)
	}
	entry.Seq = seq

	// 只有落盘成功后才更新内存状态。
	s.entries[accountID] = append(s.entries[accountID], entry)
	s.balances[accountID] = next
	if idempotencyKey != "" {
		s.idem[idemKey(accountID, idempotencyKey)] = idemRecord{entryID: entry.ID, payloadHash: payloadHash}
	}
	return entry, false, nil
}

// Balance 返回账户余额。
func (s *Service) Balance(accountID string) (int64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.accounts[accountID]; !ok {
		return 0, ErrAccountNotFound
	}
	return s.balances[accountID], nil
}

// Account 返回账户信息。
func (s *Service) Account(accountID string) (Account, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	account, ok := s.accounts[accountID]
	if !ok {
		return Account{}, ErrAccountNotFound
	}
	return account, nil
}

// Entries 返回按 seq 升序的分录，after 为排他游标。
func (s *Service) Entries(accountID string, after int64, limit int) ([]Entry, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.accounts[accountID]; !ok {
		return nil, ErrAccountNotFound
	}
	if limit <= 0 {
		limit = DefaultPageSize
	}
	if limit > MaxPageSize+1 {
		// 允许调用方多取一条用于判断是否存在下一页。
		limit = MaxPageSize + 1
	}

	all := s.entries[accountID]
	out := make([]Entry, 0, limit)
	for _, entry := range all {
		if entry.Seq <= after {
			continue
		}
		out = append(out, entry)
		if len(out) == limit {
			break
		}
	}
	return out, nil
}

// Close 关闭底层存储。
func (s *Service) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.store.Close()
}

func applyAmount(balance int64, kind Kind, amount int64) (int64, bool) {
	switch kind {
	case KindIncome:
		if amount <= 0 || balance > math.MaxInt64-amount {
			return 0, false
		}
		return balance + amount, true
	case KindExpense:
		if amount <= 0 || balance < amount {
			return 0, false
		}
		return balance - amount, true
	default:
		return 0, false
	}
}

func idemKey(accountID, key string) string {
	return accountID + "\x00" + key
}

func hashPayload(kind Kind, amount int64, memo string) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf("%s|%d|%s", kind, amount, memo)))
	return hex.EncodeToString(sum[:])
}

func newID(prefix string) string {
	buffer := make([]byte, 8)
	if _, err := rand.Read(buffer); err != nil {
		// crypto/rand 失败极罕见；用时间戳兜底，仍保证唯一性概率足够。
		return fmt.Sprintf("%s_%d", prefix, time.Now().UnixNano())
	}
	return prefix + "_" + hex.EncodeToString(buffer)
}
