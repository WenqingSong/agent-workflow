package journal

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func line(t *testing.T, seq int64, kind string, data any) string {
	t.Helper()
	payload, err := json.Marshal(Record{Seq: seq, Type: kind, Data: mustJSON(t, data)})
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
