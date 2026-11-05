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
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// ErrCorrupt 表示日志存在不可恢复的损坏。
var ErrCorrupt = errors.New("journal corrupt")

// errIncomplete 表示记录不完整（缺失校验和字段或长度不足），只可能出现在崩溃残留的末行。
var errIncomplete = errors.New("incomplete record")

// RecordVersion 是当前日志格式版本。未知版本一律 fail closed。
const RecordVersion = 1

// Record 是一条持久化事件。
type Record struct {
	Version int             `json:"version"`
	Seq     int64           `json:"seq"`
	Type    string          `json:"type"`
	Data    json.RawMessage `json:"data"`
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
	broken  error
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
			// 只有「缺少校验和 / 校验和长度不足」才可能是撕裂写残留；
			// 校验和齐全但不匹配、JSON 非法、版本未知都属于真实损坏，必须 fail closed。
			if errors.Is(err, errIncomplete) && index == len(lines)-1 {
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
	if split <= 0 {
		return rec, fmt.Errorf("%w: missing checksum separator", errIncomplete)
	}
	payload := text[:split]
	digest := text[split+1:]

	if len(digest) != 64 {
		return rec, fmt.Errorf("%w: checksum has %d characters", errIncomplete, len(digest))
	}

	sum := sha256.Sum256([]byte(payload))
	if hex.EncodeToString(sum[:]) != digest {
		// 撕裂写会同时破坏 JSON 结构：payload 不是合法 JSON 时归为「不完整」，
		// 只有结构完整但校验和不匹配才是真实损坏。
		if !json.Valid([]byte(payload)) {
			return rec, fmt.Errorf("%w: payload is not complete json", errIncomplete)
		}
		return rec, errors.New("checksum mismatch")
	}
	if err := json.Unmarshal([]byte(payload), &rec); err != nil {
		return rec, fmt.Errorf("invalid json: %w", err)
	}
	if rec.Version != RecordVersion {
		return rec, fmt.Errorf("unsupported record version %d", rec.Version)
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
//
// 写入或 fsync 失败时回滚到写入前偏移，避免半行或「已落盘但未推进 seq」
// 把一次性 I/O 错误升级为永久的 ErrCorrupt。回滚失败则把 File 置为不可用。
func (f *File) Append(rec Record) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()

	if f.broken != nil {
		return 0, f.broken
	}
	if f.file == nil {
		return 0, errors.New("journal: closed")
	}

	offset, err := f.file.Seek(0, io.SeekEnd)
	if err != nil {
		f.broken = fmt.Errorf("journal: seek: %w", err)
		return 0, f.broken
	}

	emptyBefore := f.seq == 0
	rec.Version = RecordVersion
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
		f.rollback(offset)
		return 0, fmt.Errorf("journal: write: %w", err)
	}
	if err := f.file.Sync(); err != nil {
		f.rollback(offset)
		return 0, fmt.Errorf("journal: sync: %w", err)
	}
	f.seq = rec.Seq
	f.records = append(f.records, rec)

	if emptyBefore {
		syncDir(filepath.Dir(f.path))
	}
	return rec.Seq, nil
}

// rollback 把文件恢复到写入前状态；失败则标记 File 不可继续使用。
func (f *File) rollback(offset int64) {
	if err := f.file.Truncate(offset); err != nil {
		f.broken = fmt.Errorf("journal: unusable after failed write: %w", err)
		return
	}
	if _, err := f.file.Seek(offset, io.SeekStart); err != nil {
		f.broken = fmt.Errorf("journal: unusable after failed write: %w", err)
		return
	}
	if err := f.file.Sync(); err != nil {
		// 截断未落盘时，磁盘上可能仍存在「已返回失败」的记录；拒绝后续写入。
		f.broken = fmt.Errorf("journal: unusable after failed rollback: %w", err)
	}
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
