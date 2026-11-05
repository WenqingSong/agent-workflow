// Package journal 提供 append-only、带行校验和的 JSONL 事件日志。
//
// 每行格式为 "<payload-json> <sha256-hex>\n"。加载时：
//   - 中间行损坏（JSON 非法、校验和不匹配、seq 不连续）→ 返回 ErrCorrupt，不改文件；
//   - 最后一行不完整（崩溃残留）→ 截断到最后一个完整行并返回警告记录。
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
	"sync"
)

// ErrCorrupt 表示日志中间存在不可恢复的损坏。
var ErrCorrupt = errors.New("journal corrupt")

// Record 是一条持久化事件。
type Record struct {
	Seq  int64           `json:"seq"`
	Type string          `json:"type"`
	Data json.RawMessage `json:"data"`
}

// Warning 描述加载期的非致命修复（目前只有末行截断）。
type Warning struct {
	Line   int
	Reason string
	Bytes  int64
}

// File 是 append-only 日志文件。并发安全。
type File struct {
	path    string
	mu      sync.Mutex
	file    *os.File
	seq     int64
	records []Record
}

// Open 打开或创建日志文件，并加载全部完整记录。
func Open(path string) (*File, []Warning, error) {
	if strings.TrimSpace(path) == "" {
		return nil, nil, errors.New("journal: empty path")
	}
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, nil, fmt.Errorf("journal: create dir: %w", err)
		}
	}

	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|os.O_APPEND, 0o600)
	if err != nil {
		return nil, nil, fmt.Errorf("journal: open: %w", err)
	}

	jf := &File{path: path, file: file}
	warnings, err := jf.load()
	if err != nil {
		_ = file.Close()
		return nil, nil, err
	}
	return jf, warnings, nil
}

func (f *File) load() ([]Warning, error) {
	data, err := os.ReadFile(f.path)
	if err != nil {
		return nil, fmt.Errorf("journal: read: %w", err)
	}
	if len(data) == 0 {
		return nil, nil
	}

	type line struct {
		text   string
		offset int64
	}
	var lines []line
	var offset int64
	for _, raw := range strings.SplitAfter(string(data), "\n") {
		text := strings.TrimSuffix(raw, "\n")
		if text != "" {
			lines = append(lines, line{text: text, offset: offset})
		}
		offset += int64(len(raw))
	}
	if len(lines) == 0 {
		return nil, nil
	}

	var warnings []Warning
	expectedSeq := int64(1)

	for index, item := range lines {
		rec, err := decodeLine(item.text)
		if err != nil {
			if index == len(lines)-1 {
				// 崩溃残留：截断到最后一条完整记录。
				validEnd := item.offset
				if err := f.file.Truncate(validEnd); err != nil {
					return nil, fmt.Errorf("journal: truncate torn tail: %w", err)
				}
				if err := f.file.Sync(); err != nil {
					return nil, fmt.Errorf("journal: sync after truncate: %w", err)
				}
				warnings = append(warnings, Warning{
					Line:   index + 1,
					Reason: err.Error(),
					Bytes:  int64(len(data)) - validEnd,
				})
				break
			}
			return nil, fmt.Errorf("%w: line %d: %v", ErrCorrupt, index+1, err)
		}
		if rec.Seq != expectedSeq {
			return nil, fmt.Errorf("%w: line %d: seq %d, want %d", ErrCorrupt, index+1, rec.Seq, expectedSeq)
		}
		f.records = append(f.records, rec)
		expectedSeq++
	}
	f.seq = expectedSeq - 1
	return warnings, nil
}

// Load 返回启动时加载的全部记录副本。
func (f *File) Load() ([]Record, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]Record, len(f.records))
	copy(out, f.records)
	return out, nil
}

func decodeLine(text string) (Record, error) {
	var rec Record
	split := strings.LastIndex(text, " ")
	if split <= 0 || split >= len(text)-1 {
		return rec, errors.New("missing checksum")
	}
	payload := text[:split]
	digest := text[split+1:]

	sum := sha256.Sum256([]byte(payload))
	if hex.EncodeToString(sum[:]) != digest {
		return rec, errors.New("checksum mismatch")
	}
	if err := json.Unmarshal([]byte(payload), &rec); err != nil {
		return rec, fmt.Errorf("invalid json: %w", err)
	}
	if rec.Type == "" {
		return rec, errors.New("missing record type")
	}
	if len(rec.Data) == 0 {
		return rec, errors.New("missing record data")
	}
	return rec, nil
}

// Append 写入一条记录并 fsync，返回分配的序号。
func (f *File) Append(rec Record) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()

	emptyBefore := f.seq == 0
	rec.Seq = f.seq + 1

	payload, err := json.Marshal(rec)
	if err != nil {
		return 0, fmt.Errorf("journal: marshal: %w", err)
	}
	sum := sha256.Sum256(payload)
	line := append(payload, ' ')
	line = append(line, []byte(hex.EncodeToString(sum[:]))...)
	line = append(line, '\n')

	if _, err := f.file.Write(line); err != nil {
		return 0, fmt.Errorf("journal: write: %w", err)
	}
	if err := f.file.Sync(); err != nil {
		return 0, fmt.Errorf("journal: sync: %w", err)
	}
	f.seq = rec.Seq
	f.records = append(f.records, rec)

	if emptyBefore {
		syncDir(filepath.Dir(f.path))
	}
	return rec.Seq, nil
}

// syncDir 尽力确保新建文件的目录项落盘（部分文件系统不支持目录 fsync）。
func syncDir(dir string) {
	if dir == "" {
		dir = "."
	}
	handle, err := os.Open(dir)
	if err != nil {
		return
	}
	defer handle.Close()
	_ = handle.Sync()
}

// Close 关闭日志文件。
func (f *File) Close() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.file == nil {
		return nil
	}
	err := f.file.Close()
	f.file = nil
	return err
}

// Path 返回日志路径。
func (f *File) Path() string { return f.path }
