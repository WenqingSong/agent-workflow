package journal

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func line(t *testing.T, seq int64, kind string, data any) string {
	t.Helper()
	payload, err := json.Marshal(Record{Version: RecordVersion, Seq: seq, Type: kind, Data: mustJSON(t, data)})
	if err != nil {
		t.Fatalf("marshal record: %v", err)
	}
	sum := sha256.Sum256(payload)
	return string(payload) + " " + hex.EncodeToString(sum[:]) + "\n"
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	return raw
}

func TestAppendAndReload(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")

	file, warnings, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if len(warnings) != 0 {
		t.Fatalf("unexpected warnings: %v", warnings)
	}

	first, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "a"})})
	if err != nil {
		t.Fatalf("append first: %v", err)
	}
	second, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "b"})})
	if err != nil {
		t.Fatalf("append second: %v", err)
	}
	if first != 1 || second != 2 {
		t.Fatalf("unexpected sequence: %d, %d", first, second)
	}
	if err := file.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	reopened, warnings, err := Open(path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer reopened.Close()
	if len(warnings) != 0 {
		t.Fatalf("unexpected warnings: %v", warnings)
	}
	records, err := reopened.Load()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(records) != 2 || records[0].Seq != 1 || records[1].Seq != 2 {
		t.Fatalf("unexpected records: %+v", records)
	}

	next, err := reopened.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "c"})})
	if err != nil {
		t.Fatalf("append after reload: %v", err)
	}
	if next != 3 {
		t.Fatalf("sequence must continue after reload, got %d", next)
	}
}

func TestTornTailIsTruncated(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	valid := line(t, 1, "created", map[string]string{"id": "a"})
	if err := os.WriteFile(path, []byte(valid+"{\"seq\":2,\"type\":\"crea"), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	file, warnings, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer file.Close()
	if len(warnings) != 1 {
		t.Fatalf("expected one warning, got %v", warnings)
	}
	records, err := file.Load()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(records) != 1 {
		t.Fatalf("expected only the valid record, got %d", len(records))
	}

	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if info.Size() != int64(len(valid)) {
		t.Fatalf("file must be truncated to the last complete line: size %d, want %d", info.Size(), len(valid))
	}
}

func TestMiddleCorruptionFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	content := line(t, 1, "created", map[string]string{"id": "a"}) +
		line(t, 2, "created", map[string]string{"id": "b"}) +
		line(t, 3, "created", map[string]string{"id": "c"})
	// 破坏中间行的校验和字符，保持文件结构不变。
	broken := []byte(content)
	index := len(line(t, 1, "created", map[string]string{"id": "a"})) + 5
	broken[index] = broken[index] ^ 0x01
	if err := os.WriteFile(path, broken, 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}

	_, _, err = Open(path)
	if !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt, got %v", err)
	}

	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read after: %v", err)
	}
	if string(before) != string(after) {
		t.Fatal("fail-closed must not modify the journal")
	}
}

// TestCompleteLastLineWithBadChecksumFailsClosed 覆盖审查 Finding F1：
// 末行校验和齐全但不匹配属于真实损坏，不得当作撕裂尾部截断。
func TestCompleteLastLineWithBadChecksumFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	first := line(t, 1, "created", map[string]string{"id": "a"})
	second := line(t, 2, "created", map[string]string{"id": "b"})

	// 只改 payload 中的一个字节，保留完整 64 位校验和。
	corrupted := []byte(second)
	corrupted[6] ^= 0x01

	content := first + string(corrupted)
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	before, _ := os.ReadFile(path)

	_, _, err := Open(path)
	if !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt, got %v", err)
	}
	after, _ := os.ReadFile(path)
	if string(before) != string(after) {
		t.Fatal("corrupt complete line must not be truncated")
	}
}

// TestTornTailWithChecksumLengthIsTruncated 覆盖 D4-rev2 的核心规则：
// 未终止末行的任何解码失败都按撕裂尾截断。本例用「长度恰好 64 的假校验和」
// 构造一个无论如何都无法通过校验和的末行，确认截断而非 fail closed。
func TestTornTailWithChecksumLengthIsTruncated(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	valid := line(t, 1, "created", map[string]string{"id": "a"})
	tornPayload := `{"version":1,"seq":2,"type":"created","data":{"id":"b`
	torn := tornPayload + " " + strings.Repeat("0", 64)
	if err := os.WriteFile(path, []byte(valid+torn), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	file, warnings, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer file.Close()
	if len(warnings) != 1 {
		t.Fatalf("expected one warning, got %v", warnings)
	}
	records, err := file.Load()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(records) != 1 {
		t.Fatalf("expected one valid record, got %d", len(records))
	}
}

// TestSingleCorruptLineIsNotCleared 覆盖审查 Finding F1：
// 单行账本损坏时不得把文件截断为 0 字节。
func TestSingleCorruptLineIsNotCleared(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	only := []byte(line(t, 1, "created", map[string]string{"id": "a"}))
	only[6] ^= 0x01
	if err := os.WriteFile(path, only, 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	_, _, err := Open(path)
	if !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt, got %v", err)
	}
	info, statErr := os.Stat(path)
	if statErr != nil {
		t.Fatalf("stat: %v", statErr)
	}
	if info.Size() == 0 {
		t.Fatal("single corrupt record must not clear the journal")
	}
}

// TestStructuralCorruptionOnTerminatedLineFailsClosed 覆盖第三轮审查 H1：
// 已换行终止的行即使 JSON 结构被破坏，也属于真实损坏，不得截断清空。
func TestStructuralCorruptionOnTerminatedLineFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	only := []byte(line(t, 1, "created", map[string]string{"id": "a"}))
	// 破坏首字符 '{'，使 payload 不再是合法 JSON，但保留换行终止。
	only[0] = 'z'
	if err := os.WriteFile(path, only, 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	before, _ := os.ReadFile(path)

	_, _, err := Open(path)
	if !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt, got %v", err)
	}
	after, _ := os.ReadFile(path)
	if string(before) != string(after) {
		t.Fatal("terminated corrupt line must not be truncated")
	}
}

// TestChecksumValidButInvalidJSON 覆盖第四轮审查 I5：
// 校验和正确但 payload 非法 JSON 时，已终止行 fail closed，未终止行按撕裂尾截断。
func TestChecksumValidButInvalidJSON(t *testing.T) {
	payload := `{"version":1,"seq":1,"type":"created","data":`
	sum := sha256.Sum256([]byte(payload))
	checksum := hex.EncodeToString(sum[:])

	t.Run("terminated", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "ledger.jsonl")
		content := payload + " " + checksum + "\n"
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatalf("write fixture: %v", err)
		}
		if _, _, err := Open(path); !errors.Is(err, ErrCorrupt) {
			t.Fatalf("expected ErrCorrupt, got %v", err)
		}
	})

	t.Run("unterminated", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "ledger.jsonl")
		content := payload + " " + checksum
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatalf("write fixture: %v", err)
		}
		file, warnings, err := Open(path)
		if err != nil {
			t.Fatalf("open: %v", err)
		}
		defer file.Close()
		if len(warnings) != 1 || warnings[0].Kind != WarningTornTail {
			t.Fatalf("expected torn tail warning, got %+v", warnings)
		}
		info, _ := os.Stat(path)
		if info.Size() != 0 {
			t.Fatalf("torn record must be truncated, size=%d", info.Size())
		}
	})
}

// TestBlankLineFailsClosed 覆盖第四轮审查 I4：中间空行按损坏处理。
func TestBlankLineFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	content := line(t, 1, "created", map[string]string{"id": "a"}) + "\n" +
		line(t, 2, "created", map[string]string{"id": "b"})
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	before, _ := os.ReadFile(path)
	if _, _, err := Open(path); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt for blank line, got %v", err)
	}
	after, _ := os.ReadFile(path)
	if string(before) != string(after) {
		t.Fatal("blank line failure must not modify the journal")
	}
}

// TestBlankLineOnlyFileFailsClosed 覆盖第六轮审查 K1：
// 只含换行的文件不是空账本，必须在加载时拒绝。
func TestBlankLineOnlyFileFailsClosed(t *testing.T) {
	for _, content := range []string{"\n", "\n\n", "\n\n\n"} {
		t.Run(fmt.Sprintf("%q", content), func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "ledger.jsonl")
			if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
				t.Fatalf("write fixture: %v", err)
			}
			before, _ := os.ReadFile(path)
			if _, _, err := Open(path); !errors.Is(err, ErrCorrupt) {
				t.Fatalf("expected ErrCorrupt, got %v", err)
			}
			after, _ := os.ReadFile(path)
			if string(before) != string(after) {
				t.Fatal("blank-only file must not be modified")
			}
		})
	}
}

// TestTrailingBlankLineFailsClosed 覆盖第五轮审查 J1：
// 尾部多余空行会让下一次追加后变成中间空行，必须在加载时拒绝。
func TestTrailingBlankLineFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	content := line(t, 1, "created", map[string]string{"id": "a"}) + "\n"
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	before, _ := os.ReadFile(path)
	if _, _, err := Open(path); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt for trailing blank line, got %v", err)
	}
	after, _ := os.ReadFile(path)
	if string(before) != string(after) {
		t.Fatal("trailing blank line must not modify the journal")
	}
}

// TestSingleUnterminatedCorruptLineIsTruncated 覆盖第四轮审查 I3：
// 单行 + 无换行 + 损坏时按撕裂尾截断，行为被测试固化（不是静默丢一个完整记录）。
func TestSingleUnterminatedCorruptLineIsTruncated(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	only := strings.TrimSuffix(line(t, 1, "created", map[string]string{"id": "a"}), "\n")
	corrupted := []byte(only)
	corrupted[6] ^= 0x01
	if err := os.WriteFile(path, corrupted, 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	file, warnings, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer file.Close()
	if len(warnings) != 1 || warnings[0].Kind != WarningTornTail {
		t.Fatalf("expected torn tail warning, got %+v", warnings)
	}
	info, _ := os.Stat(path)
	if info.Size() != 0 {
		t.Fatalf("torn record must be truncated, size=%d", info.Size())
	}
}

// TestMissingTrailingNewlineIsRepaired 覆盖第三轮审查 H2：
// 记录完整但缺少换行时补齐，避免下一次追加与上一行粘连。
func TestMissingTrailingNewlineIsRepaired(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	first := line(t, 1, "created", map[string]string{"id": "a"})
	if err := os.WriteFile(path, []byte(strings.TrimSuffix(first, "\n")), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	file, warnings, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if len(warnings) != 1 || warnings[0].Kind != WarningRepairedNewline {
		t.Fatalf("expected repaired_newline warning, got %+v", warnings)
	}
	if _, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "b"})}); err != nil {
		t.Fatalf("append after repair: %v", err)
	}
	if err := file.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	reopened, warnings, err := Open(path)
	if err != nil {
		t.Fatalf("reopen: %v (repair must prevent line concatenation)", err)
	}
	defer reopened.Close()
	if len(warnings) != 0 {
		t.Fatalf("unexpected warnings after repair: %v", warnings)
	}
	records, err := reopened.Load()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(records))
	}
}

// TestRollbackKeepsJournalUsable 覆盖第三轮审查 H3：
// 写入失败但回滚成功时，后续追加仍可用且 seq 不重复。
func TestRollbackKeepsJournalUsable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	file, _, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if _, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "a"})}); err != nil {
		t.Fatalf("append: %v", err)
	}

	file.afterWrite = func() error { return errors.New("injected io failure") }
	if _, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "b"})}); err == nil {
		t.Fatal("injected failure must surface")
	}
	file.afterWrite = nil

	seq, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "c"})})
	if err != nil {
		t.Fatalf("append after rollback: %v", err)
	}
	if seq != 2 {
		t.Fatalf("seq must not skip or reuse: got %d, want 2", seq)
	}
	if err := file.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	reopened, _, err := Open(path)
	if err != nil {
		t.Fatalf("reopen after rollback: %v", err)
	}
	defer reopened.Close()
	records, err := reopened.Load()
	if err != nil {
		t.Fatalf("load: %v", err)
	}
	if len(records) != 2 {
		t.Fatalf("failed write must be rolled back, got %d records", len(records))
	}
}

// TestAppendFailureMarksJournalUnusable 覆盖审查 Finding F2：
// 写入失败后不得在不确定的文件位置上继续追加。
func TestAppendFailureMarksJournalUnusable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	file, _, err := Open(path)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	if _, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "a"})}); err != nil {
		t.Fatalf("append: %v", err)
	}

	// 直接关闭底层句柄，模拟写入时的 I/O 故障。
	if err := file.file.Close(); err != nil {
		t.Fatalf("close handle: %v", err)
	}
	if _, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "b"})}); err == nil {
		t.Fatal("append on closed file must fail")
	}
	if _, err := file.Append(Record{Type: "created", Data: mustJSON(t, map[string]string{"id": "c"})}); err == nil {
		t.Fatal("journal must stay unusable after a failed write instead of reusing seq")
	}
}

func TestSequenceGapDetected(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	content := line(t, 1, "created", map[string]string{"id": "a"}) +
		line(t, 3, "created", map[string]string{"id": "c"})
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	_, _, err := Open(path)
	if !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt for sequence gap, got %v", err)
	}
}

func TestMissingChecksumOnMiddleLineFails(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ledger.jsonl")
	content := line(t, 1, "created", map[string]string{"id": "a"}) +
		"{\"seq\":2,\"type\":\"created\",\"data\":{\"id\":\"b\"}}\n" +
		line(t, 3, "created", map[string]string{"id": "c"})
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	_, _, err := Open(path)
	if !errors.Is(err, ErrCorrupt) {
		t.Fatalf("expected ErrCorrupt, got %v", err)
	}
}
