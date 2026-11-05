package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

var binaryPath string

func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "ledgerd-bin-*")
	if err != nil {
		fmt.Fprintf(os.Stderr, "temp dir: %v\n", err)
		os.Exit(1)
	}
	binaryPath = filepath.Join(dir, "ledgerd")

	build := exec.Command("go", "build", "-trimpath", "-o", binaryPath, ".")
	build.Env = append(os.Environ(), "GOWORK=off")
	if output, err := build.CombinedOutput(); err != nil {
		fmt.Fprintf(os.Stderr, "build failed: %v\n%s\n", err, output)
		os.Exit(1)
	}

	code := m.Run()
	_ = os.RemoveAll(dir)
	os.Exit(code)
}

type running struct {
	cmd      *exec.Cmd
	base     string
	stderr   *bytes.Buffer
	done     chan error
	finished chan struct{}
}

func startServer(t *testing.T, dataPath string) *running {
	t.Helper()
	cmd := exec.Command(binaryPath, "--addr", "127.0.0.1:0", "--data", dataPath, "--shutdown-timeout", "5s")
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatalf("stdout pipe: %v", err)
	}
	stderr := &bytes.Buffer{}
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("start: %v", err)
	}

	addrCh := make(chan string, 1)
	go func() {
		reader := bufio.NewReader(stdout)
		for {
			line, err := reader.ReadString('\n')
			if strings.HasPrefix(line, "LEDGERD_ADDR=") {
				addrCh <- strings.TrimSpace(strings.TrimPrefix(line, "LEDGERD_ADDR="))
				return
			}
			if err != nil {
				return
			}
		}
	}()

	select {
	case addr := <-addrCh:
		proc := &running{
			cmd:      cmd,
			base:     "http://" + addr,
			stderr:   stderr,
			done:     make(chan error, 1),
			finished: make(chan struct{}),
		}
		go func() {
			proc.done <- cmd.Wait()
			close(proc.finished)
		}()
		t.Cleanup(func() {
			select {
			case <-proc.finished:
				return
			default:
			}
			_ = cmd.Process.Kill()
			select {
			case <-proc.finished:
			case <-time.After(2 * time.Second):
			}
		})
		return proc
	case <-time.After(10 * time.Second):
		_ = cmd.Process.Kill()
		t.Fatalf("server did not report address; stderr=%s", stderr.String())
		return nil
	}
}

func (r *running) stopGracefully(t *testing.T) {
	t.Helper()
	if err := r.cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatalf("signal: %v", err)
	}
	select {
	case err := <-r.done:
		if err != nil {
			t.Fatalf("graceful shutdown returned error: %v; stderr=%s", err, r.stderr.String())
		}
	case <-time.After(15 * time.Second):
		t.Fatalf("server did not exit after SIGTERM; stderr=%s", r.stderr.String())
	}
}

func (r *running) killHard(t *testing.T) {
	t.Helper()
	if err := r.cmd.Process.Kill(); err != nil {
		t.Fatalf("kill: %v", err)
	}
	<-r.done
}

func request(t *testing.T, method, url string, body any) (int, map[string]any) {
	t.Helper()
	return requestWithHeaders(t, method, url, body, nil)
}

func requestWithHeaders(t *testing.T, method, url string, body any, headers map[string]string) (int, map[string]any) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal: %v", err)
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, url, reader)
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for key, value := range headers {
		req.Header.Set(key, value)
	}
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	defer resp.Body.Close()

	payload := map[string]any{}
	raw, _ := io.ReadAll(resp.Body)
	if len(bytes.TrimSpace(raw)) > 0 {
		if err := json.Unmarshal(raw, &payload); err != nil {
			t.Fatalf("decode: %v (%s)", err, raw)
		}
	}
	return resp.StatusCode, payload
}

func createAccount(t *testing.T, base, name string) string {
	t.Helper()
	status, payload := request(t, http.MethodPost, base+"/accounts", map[string]any{"name": name})
	if status != http.StatusCreated {
		t.Fatalf("create account: %d %v", status, payload)
	}
	return payload["id"].(string)
}

// TestGracefulShutdownAndRestart 验证 AC-5/AC-8：
// 新进程重启后数据完整，SIGTERM 以退出码 0 结束。
func TestGracefulShutdownAndRestart(t *testing.T) {
	dataPath := filepath.Join(t.TempDir(), "ledger.jsonl")

	first := startServer(t, dataPath)
	accountID := createAccount(t, first.base, "日常")
	status, income := request(t, http.MethodPost, first.base+"/accounts/"+accountID+"/entries", map[string]any{
		"kind": "income", "amount_cents": 10000, "memo": "工资",
	})
	if status != http.StatusCreated {
		t.Fatalf("post income: %d %v", status, income)
	}
	if status, _ := request(t, http.MethodPost, first.base+"/accounts/"+accountID+"/entries", map[string]any{
		"kind": "expense", "amount_cents": 2500, "memo": "房租",
	}); status != http.StatusCreated {
		t.Fatalf("post expense: %d", status)
	}
	first.stopGracefully(t)

	second := startServer(t, dataPath)
	status, balance := request(t, http.MethodGet, second.base+"/accounts/"+accountID+"/balance", nil)
	if status != http.StatusOK {
		t.Fatalf("balance after restart: %d %v", status, balance)
	}
	if balance["balance_cents"].(float64) != 7500 {
		t.Fatalf("balance after restart: %v", balance)
	}

	status, listing := request(t, http.MethodGet, second.base+"/accounts/"+accountID+"/entries", nil)
	if status != http.StatusOK {
		t.Fatalf("entries after restart: %d %v", status, listing)
	}
	if len(listing["items"].([]any)) != 2 {
		t.Fatalf("entries after restart: %v", listing["items"])
	}
	second.stopGracefully(t)
}

// TestIdempotencySurvivesRestart 验证 AC-6 的「幂等键跨重启有效」：
// 进程 A 记账后退出，进程 B 用同一键与载荷重放，必须返回同一分录且余额只变一次。
func TestIdempotencySurvivesRestart(t *testing.T) {
	dataPath := filepath.Join(t.TempDir(), "ledger.jsonl")
	headers := map[string]string{"Idempotency-Key": "restart-key"}

	first := startServer(t, dataPath)
	accountID := createAccount(t, first.base, "idem")
	status, entry := requestWithHeaders(t, http.MethodPost, first.base+"/accounts/"+accountID+"/entries", map[string]any{
		"kind": "income", "amount_cents": 4200, "memo": "bonus",
	}, headers)
	if status != http.StatusCreated {
		t.Fatalf("post: %d %v", status, entry)
	}
	first.stopGracefully(t)

	second := startServer(t, dataPath)
	status, replay := requestWithHeaders(t, http.MethodPost, second.base+"/accounts/"+accountID+"/entries", map[string]any{
		"kind": "income", "amount_cents": 4200, "memo": "bonus",
	}, headers)
	if status != http.StatusOK {
		t.Fatalf("replay after restart must return 200, got %d %v", status, replay)
	}
	if replay["entry_id"] != entry["entry_id"] {
		t.Fatalf("replay must return the same entry: %v vs %v", replay["entry_id"], entry["entry_id"])
	}
	if replay["replayed"] != true {
		t.Fatalf("replayed flag missing: %v", replay)
	}

	_, balance := request(t, http.MethodGet, second.base+"/accounts/"+accountID+"/balance", nil)
	if balance["balance_cents"].(float64) != 4200 {
		t.Fatalf("balance must change exactly once: %v", balance)
	}
	_, listing := request(t, http.MethodGet, second.base+"/accounts/"+accountID+"/entries", nil)
	if len(listing["items"].([]any)) != 1 {
		t.Fatalf("replay must not append a second entry: %v", listing["items"])
	}
	second.stopGracefully(t)
}

// TestKillNineKeepsAcknowledgedEntry 验证已返回 2xx 的分录在 kill -9 后仍可被新进程读到。
//
// 注意：SIGKILL 不会清空 OS 页缓存，因此该用例证明的是「跨进程可见 + 日志格式自洽」，
// **不能**证明 fsync 落盘。fsync 的正确性由代码路径（Write → Sync → 才返回）与审查结论承担。
func TestKillNineKeepsAcknowledgedEntry(t *testing.T) {
	dataPath := filepath.Join(t.TempDir(), "ledger.jsonl")

	first := startServer(t, dataPath)
	accountID := createAccount(t, first.base, "crash")
	status, entry := request(t, http.MethodPost, first.base+"/accounts/"+accountID+"/entries", map[string]any{
		"kind": "income", "amount_cents": 9900, "memo": "before crash",
	})
	if status != http.StatusCreated {
		t.Fatalf("post: %d %v", status, entry)
	}
	first.killHard(t)

	second := startServer(t, dataPath)
	status, balance := request(t, http.MethodGet, second.base+"/accounts/"+accountID+"/balance", nil)
	if status != http.StatusOK {
		t.Fatalf("balance after kill -9: %d %v", status, balance)
	}
	if balance["balance_cents"].(float64) != 9900 {
		t.Fatalf("acknowledged entry lost: %v", balance)
	}
	second.stopGracefully(t)
}

// TestCorruptJournalFailsClosed 验证 AC-9：中间行损坏时进程非 0 退出且文件不被修改。
func TestCorruptJournalFailsClosed(t *testing.T) {
	dataPath := filepath.Join(t.TempDir(), "ledger.jsonl")

	first := startServer(t, dataPath)
	accountID := createAccount(t, first.base, "corrupt")
	for index := 0; index < 3; index++ {
		status, payload := request(t, http.MethodPost, first.base+"/accounts/"+accountID+"/entries", map[string]any{
			"kind": "income", "amount_cents": 100,
		})
		if status != http.StatusCreated {
			t.Fatalf("post %d: %d %v", index, status, payload)
		}
	}
	first.stopGracefully(t)

	raw, err := os.ReadFile(dataPath)
	if err != nil {
		t.Fatalf("read journal: %v", err)
	}
	lines := bytes.SplitAfter(raw, []byte("\n"))
	if len(lines) < 3 {
		t.Fatalf("unexpected journal shape: %d lines", len(lines))
	}
	// 破坏第二行的校验和（不是最后一行，因此属于真实损坏）。
	second := lines[1]
	marker := bytes.LastIndexByte(second, ' ')
	if marker < 0 {
		t.Fatal("cannot locate checksum separator")
	}
	second[marker+1] ^= 0x01
	corrupted := append(append(append([]byte{}, lines[0]...), second...), bytes.Join(lines[2:], nil)...)
	if err := os.WriteFile(dataPath, corrupted, 0o600); err != nil {
		t.Fatalf("write corrupt journal: %v", err)
	}

	before, _ := os.ReadFile(dataPath)
	cmd := exec.Command(binaryPath, "--addr", "127.0.0.1:0", "--data", dataPath)
	output := &bytes.Buffer{}
	cmd.Stdout = output
	cmd.Stderr = output
	err = cmd.Run()
	if err == nil {
		t.Fatal("corrupt journal must fail closed")
	}
	exitErr, ok := err.(*exec.ExitError)
	if !ok || exitErr.ExitCode() == 0 {
		t.Fatalf("expected non-zero exit, got %v", err)
	}
	if !strings.Contains(output.String(), "corrupt") {
		t.Fatalf("error must mention corruption: %s", output.String())
	}

	after, _ := os.ReadFile(dataPath)
	if !bytes.Equal(before, after) {
		t.Fatal("fail closed must not rewrite the journal")
	}
}
