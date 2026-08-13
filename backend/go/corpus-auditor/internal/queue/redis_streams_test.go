package queue

import "testing"

func TestParseCommandMessagesRejectsMalformedPayloads(t *testing.T) {
	t.Parallel()
	_, err := parseMessageList([]any{
		[]any{"1-0", []any{payloadField, `{"schema_version":1}`}},
	})
	if err == nil {
		t.Fatal("command without an ID was accepted")
	}
	_, err = parseMessageList([]any{
		[]any{"1-0", []any{payloadField, `{"id":"job","schema_version":2}`}},
	})
	if err == nil {
		t.Fatal("unsupported schema was accepted")
	}
}

func TestParseRedisURLSupportsRedisAndTLS(t *testing.T) {
	t.Parallel()
	secure, err := parseRedisURL("rediss://redis.example:6380/0")
	if err != nil || !secure.tls {
		t.Fatalf("TLS Redis URL = %#v, %v", secure, err)
	}
	endpoint, err := parseRedisURL("redis://user:secret@redis.example:6379/2")
	if err != nil {
		t.Fatal(err)
	}
	if endpoint.address != "redis.example:6379" || endpoint.username != "user" || endpoint.password != "secret" || endpoint.database != 2 {
		t.Fatalf("unexpected endpoint: %#v", endpoint)
	}
}
