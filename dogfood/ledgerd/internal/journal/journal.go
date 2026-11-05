// Package journal 提供 append-only、带行校验和的 JSONL 事件日志。
//
// 每行格式为 "<payload-json> <sha256-hex>\n"。加载时按「末行是否以换行终止」区分
// 崩溃残留与真实损坏：
//   - 已换行终止的行损坏（校验和不匹配、JSON 非法、seq 不连续、版本未知、空行）
//     → 返回 ErrCorrupt，不修改文件；
//   - 未终止的末行解码失败 → 视为撕裂写，截断到最后一个完整行并返回 torn_tail 警告；
//   - 未终止的末行解码成功 → 补写换行并返回 repaired_newline 警告，避免后续追加粘连。
//
// 边界：未终止末行中的任何损坏都会被当作撕裂写截断（包括校验和齐全但不匹配），
// 因此该边界不提供「拒绝启动」保护，只保证不把损坏行当作有效记录。
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

// 告警类型。
const (
	WarningTornTail        = "torn_tail"
	WarningRepairedNewline = "repaired_newline"
)

// Warning 描述加载期的非致命修复。
type Warning struct {
	Kind   string
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

	// afterWrite 仅供测试注入故障，生产路径为 nil。
	afterWrite func() error
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
		text       string
		offset     int64
		terminated bool
		number     int
	}

	segments := strings.SplitAfter(string(data), "\n")
	lastNonEmpty := -1
	for index, raw := range segments {
		if strings.TrimSuffix(raw, "\n") != "" {
			lastNonEmpty = index
		}
	}
	if lastNonEmpty < 0 {
		// 完全空文件是合法的空账本；只含空行的文件不是。
		if len(data) > 0 {
			return nil, fmt.Errorf("%w: line 1: file contains blank lines only", ErrCorrupt)
		}
		return nil, nil
	}

	// 正常文件只允许由末尾换行产生一个空段；多余的空段意味着存在尾部空行，
	// 会让下一次追加后变成中间空行，必须立即 fail closed。
	for _, raw := range segments[lastNonEmpty+1:] {
		if raw != "" {
			return nil, fmt.Errorf(
				"%w: line %d: trailing blank line",
				ErrCorrupt, lastNonEmpty+2,
			)
		}
	}

	var lines []line
	var offset int64
	for index, raw := range segments[:lastNonEmpty+1] {
		text := strings.TrimSuffix(raw, "\n")
		if text == "" {
			// 中间空行是异常痕迹（正常写入不会产生），一律 fail closed。
			return nil, fmt.Errorf("%w: line %d: unexpected blank line", ErrCorrupt, index+1)
		}
		lines = append(lines, line{
			text:       text,
			offset:     offset,
			terminated: strings.HasSuffix(raw, "\n"),
			number:     index + 1,
		})
		offset += int64(len(raw))
	}

	var warnings []Warning
	expectedSeq := int64(1)

	for index, item := range lines {
		rec, err := decodeLine(item.text)
		if err != nil {
			// 只有「最后一行且没有以换行结束」才可能是崩溃撕裂写残留；
			// 已换行终止的行即使损坏，也属于真实损坏，必须 fail closed。
			if index == len(lines)-1 && !item.terminated {
				validEnd := item.offset
				if err := f.file.Truncate(validEnd); err != nil {
					return nil, fmt.Errorf("journal: truncate torn tail: %w", err)
				}
				if err := f.file.Sync(); err != nil {
					return nil, fmt.Errorf("journal: sync after truncate: %w", err)
				}
				warnings = append(warnings, Warning{
					Kind:   WarningTornTail,
					Line:   item.number,
					Reason: err.Error(),
					Bytes:  int64(len(data)) - validEnd,
				})
				break
			}
			return nil, fmt.Errorf("%w: line %d: %v", ErrCorrupt, item.number, err)
		}
		if rec.Seq != expectedSeq {
			return nil, fmt.Errorf("%w: line %d: seq %d, want %d", ErrCorrupt, item.number, rec.Seq, expectedSeq)
		}
		f.records = append(f.records, rec)
		if index == len(lines)-1 && !item.terminated {
			// 记录完整但缺少终止换行：补齐，避免下一次追加与上一行粘连。
			if _, err := f.file.Write([]byte("\n")); err != nil {
				return nil, fmt.Errorf("journal: repair trailing newline: %w", err)
			}
			if err := f.file.Sync(); err != nil {
				return nil, fmt.Errorf("journal: sync after newline repair: %w", err)
			}
			warnings = append(warnings, Warning{
				Kind:   WarningRepairedNewline,
				Line:   item.number,
				Reason: "missing trailing newline (repaired)",
			})
		}
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
	if f.afterWrite != nil {
		if err := f.afterWrite(); err != nil {
			f.rollback(offset)
			return 0, fmt.Errorf("journal: write failed: %w", err)
		}
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
