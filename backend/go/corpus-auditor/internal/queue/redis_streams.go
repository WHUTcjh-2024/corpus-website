package queue

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"strconv"
	"strings"
	"time"

	"corpus-platform/corpus-auditor/internal/service"
)

const payloadField = "payload"

// Config covers the small Redis Streams surface needed by this worker. The
// implementation deliberately uses only the Go standard library so the data
// plane remains buildable in restricted production build environments.
type Config struct {
	URL            string
	CommandStream  string
	CommandGroup   string
	Consumer       string
	ResultStream   string
	StreamMaxLen   int64
	Block          time.Duration
	ClaimIdle      time.Duration
	ReadCount      int64
	OperationLimit time.Duration
}

func (config Config) withDefaults() Config {
	if config.StreamMaxLen < 100 {
		config.StreamMaxLen = 100_000
	}
	if config.Block <= 0 {
		config.Block = time.Second
	}
	if config.ClaimIdle <= 0 {
		config.ClaimIdle = 30 * time.Second
	}
	if config.ReadCount < 1 {
		config.ReadCount = 10
	}
	if config.OperationLimit <= 0 {
		config.OperationLimit = 5 * time.Second
	}
	return config
}

func (config Config) Validate() error {
	if strings.TrimSpace(config.URL) == "" || strings.TrimSpace(config.CommandStream) == "" || strings.TrimSpace(config.CommandGroup) == "" || strings.TrimSpace(config.Consumer) == "" || strings.TrimSpace(config.ResultStream) == "" {
		return errors.New("redis URL, streams, group, and consumer are required")
	}
	return nil
}

type Streams struct {
	config   Config
	endpoint redisEndpoint
}

func New(config Config) (*Streams, error) {
	config = config.withDefaults()
	if err := config.Validate(); err != nil {
		return nil, err
	}
	endpoint, err := parseRedisURL(config.URL)
	if err != nil {
		return nil, err
	}
	return &Streams{config: config, endpoint: endpoint}, nil
}

func (streams *Streams) Close() error { return nil }

// Ping confirms that Redis is available before the service declares itself
// ready to consume commands. It intentionally does not create streams/groups.
func (streams *Streams) Ping(ctx context.Context) error {
	_, err := streams.command(ctx, "PING")
	return err
}

func (streams *Streams) EnsureCommandGroup(ctx context.Context) error {
	_, err := streams.command(ctx, "XGROUP", "CREATE", streams.config.CommandStream, streams.config.CommandGroup, "0", "MKSTREAM")
	if err != nil && !strings.Contains(err.Error(), "BUSYGROUP") {
		return fmt.Errorf("create command group: %w", err)
	}
	return nil
}

func (streams *Streams) ReadCommands(ctx context.Context) ([]Message, error) {
	value, err := streams.command(ctx, "XREADGROUP", "GROUP", streams.config.CommandGroup, streams.config.Consumer, "COUNT", strconv.FormatInt(streams.config.ReadCount, 10), "BLOCK", strconv.FormatInt(streams.config.Block.Milliseconds(), 10), "STREAMS", streams.config.CommandStream, ">")
	if err != nil {
		return nil, fmt.Errorf("read command stream: %w", err)
	}
	return parseStreamMessages(value)
}

func (streams *Streams) ReclaimCommands(ctx context.Context) ([]Message, error) {
	value, err := streams.command(ctx, "XAUTOCLAIM", streams.config.CommandStream, streams.config.CommandGroup, streams.config.Consumer, strconv.FormatInt(streams.config.ClaimIdle.Milliseconds(), 10), "0-0", "COUNT", strconv.FormatInt(streams.config.ReadCount, 10))
	if err != nil {
		return nil, fmt.Errorf("reclaim command stream: %w", err)
	}
	array, ok := value.([]any)
	if !ok || len(array) < 2 {
		return nil, errors.New("invalid XAUTOCLAIM response")
	}
	return parseMessageList(array[1])
}

func (streams *Streams) AckCommand(ctx context.Context, messageID string) error {
	_, err := streams.command(ctx, "XACK", streams.config.CommandStream, streams.config.CommandGroup, messageID)
	return err
}

func (streams *Streams) Publish(payload []byte) error {
	if len(payload) == 0 || len(payload) > 1<<20 {
		return errors.New("result payload must contain 1 to 1048576 bytes")
	}
	context, cancel := context.WithTimeout(context.Background(), streams.config.OperationLimit)
	defer cancel()
	_, err := streams.command(context, "XADD", streams.config.ResultStream, "MAXLEN", "~", strconv.FormatInt(streams.config.StreamMaxLen, 10), "*", payloadField, string(payload))
	return err
}

func (streams *Streams) command(ctx context.Context, arguments ...string) (any, error) {
	dialer := net.Dialer{Timeout: streams.config.OperationLimit}
	connection, err := dialer.DialContext(ctx, "tcp", streams.endpoint.address)
	if err != nil {
		return nil, err
	}
	defer connection.Close()
	if streams.endpoint.tls {
		serverName, _, _ := net.SplitHostPort(streams.endpoint.address)
		secure := tls.Client(connection, &tls.Config{MinVersion: tls.VersionTLS12, ServerName: serverName})
		if err := secure.HandshakeContext(ctx); err != nil {
			return nil, fmt.Errorf("establish Redis TLS: %w", err)
		}
		connection = secure
	}
	deadline := time.Now().Add(streams.config.OperationLimit)
	if until, ok := ctx.Deadline(); ok && until.Before(deadline) {
		deadline = until
	}
	if err := connection.SetDeadline(deadline); err != nil {
		return nil, err
	}
	reader := bufio.NewReader(connection)
	if streams.endpoint.password != "" {
		if _, err := writeCommand(connection, "AUTH", streams.endpoint.username, streams.endpoint.password); err != nil {
			return nil, err
		}
		if _, err := readRESP(reader); err != nil {
			return nil, err
		}
	}
	if streams.endpoint.database != 0 {
		if _, err := writeCommand(connection, "SELECT", strconv.Itoa(streams.endpoint.database)); err != nil {
			return nil, err
		}
		if _, err := readRESP(reader); err != nil {
			return nil, err
		}
	}
	if _, err := writeCommand(connection, arguments...); err != nil {
		return nil, err
	}
	return readRESP(reader)
}

type redisEndpoint struct {
	address, username, password string
	database                    int
	tls                         bool
}

func parseRedisURL(raw string) (redisEndpoint, error) {
	parsed, err := url.Parse(raw)
	if err != nil || (parsed.Scheme != "redis" && parsed.Scheme != "rediss") {
		return redisEndpoint{}, errors.New("AUDITOR_REDIS_URL must be redis:// or rediss://")
	}
	address := parsed.Host
	if address == "" {
		return redisEndpoint{}, errors.New("Redis URL requires a host")
	}
	if !strings.Contains(address, ":") {
		address += ":6379"
	}
	database := 0
	if path := strings.TrimPrefix(parsed.Path, "/"); path != "" {
		database, err = strconv.Atoi(path)
		if err != nil || database < 0 {
			return redisEndpoint{}, errors.New("Redis URL database is invalid")
		}
	}
	username, password := "default", ""
	if parsed.User != nil {
		username = parsed.User.Username()
		password, _ = parsed.User.Password()
	}
	return redisEndpoint{address: address, username: username, password: password, database: database, tls: parsed.Scheme == "rediss"}, nil
}

func writeCommand(writer io.Writer, arguments ...string) (int, error) {
	var builder strings.Builder
	fmt.Fprintf(&builder, "*%d\r\n", len(arguments))
	for _, argument := range arguments {
		fmt.Fprintf(&builder, "$%d\r\n%s\r\n", len(argument), argument)
	}
	return io.WriteString(writer, builder.String())
}

func readRESP(reader *bufio.Reader) (any, error) {
	prefix, err := reader.ReadByte()
	if err != nil {
		return nil, err
	}
	line, err := readLine(reader)
	if err != nil {
		return nil, err
	}
	switch prefix {
	case '+':
		return line, nil
	case '-':
		return nil, errors.New(line)
	case ':':
		return strconv.ParseInt(line, 10, 64)
	case '$':
		length, err := strconv.Atoi(line)
		if err != nil {
			return nil, err
		}
		if length == -1 {
			return nil, nil
		}
		data := make([]byte, length+2)
		if _, err := io.ReadFull(reader, data); err != nil {
			return nil, err
		}
		return string(data[:length]), nil
	case '*':
		length, err := strconv.Atoi(line)
		if err != nil {
			return nil, err
		}
		if length == -1 {
			return nil, nil
		}
		array := make([]any, length)
		for index := range array {
			if array[index], err = readRESP(reader); err != nil {
				return nil, err
			}
		}
		return array, nil
	default:
		return nil, errors.New("unsupported Redis response")
	}
}

func readLine(reader *bufio.Reader) (string, error) {
	value, err := reader.ReadString('\n')
	if err != nil {
		return "", err
	}
	return strings.TrimSuffix(strings.TrimSuffix(value, "\n"), "\r"), nil
}

type Message struct {
	ID      string
	Request service.SubmitRequest
}

func parseStreamMessages(value any) ([]Message, error) {
	if value == nil {
		return nil, nil
	}
	streams, ok := value.([]any)
	if !ok {
		return nil, errors.New("invalid stream response")
	}
	messages := make([]Message, 0)
	for _, stream := range streams {
		pair, ok := stream.([]any)
		if !ok || len(pair) != 2 {
			return nil, errors.New("invalid stream entry")
		}
		parsed, err := parseMessageList(pair[1])
		if err != nil {
			return nil, err
		}
		messages = append(messages, parsed...)
	}
	return messages, nil
}

func parseMessageList(value any) ([]Message, error) {
	if value == nil {
		return nil, nil
	}
	entries, ok := value.([]any)
	if !ok {
		return nil, errors.New("invalid message list")
	}
	messages := make([]Message, 0, len(entries))
	for _, entry := range entries {
		pair, ok := entry.([]any)
		if !ok || len(pair) != 2 {
			return nil, errors.New("invalid stream message")
		}
		id, ok := pair[0].(string)
		if !ok {
			return nil, errors.New("stream message has invalid ID")
		}
		fields, ok := pair[1].([]any)
		if !ok || len(fields)%2 != 0 {
			return nil, errors.New("stream message has invalid fields")
		}
		payload := ""
		for index := 0; index < len(fields); index += 2 {
			key, keyOK := fields[index].(string)
			value, valueOK := fields[index+1].(string)
			if keyOK && valueOK && key == payloadField {
				payload = value
			}
		}
		if len(payload) == 0 || len(payload) > 1<<20 {
			return nil, fmt.Errorf("command %s payload size is invalid", id)
		}
		var command struct {
			ID            string               `json:"id"`
			SchemaVersion int                  `json:"schema_version"`
			InputRef      string               `json:"input_ref"`
			OutputPrefix  string               `json:"output_prefix"`
			Options       service.AuditOptions `json:"options"`
		}
		if err := json.Unmarshal([]byte(payload), &command); err != nil || command.SchemaVersion != 1 || command.ID == "" {
			return nil, fmt.Errorf("command %s has invalid schema", id)
		}
		messages = append(messages, Message{ID: id, Request: service.SubmitRequest{JobID: command.ID, InputRef: command.InputRef, OutputPrefix: command.OutputPrefix, Options: command.Options}})
	}
	return messages, nil
}
