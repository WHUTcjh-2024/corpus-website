package service

// ResultPublisher is deliberately narrow: execution only needs to publish a
// terminal JSON document. It never receives Python credentials or DB access.
type ResultPublisher interface {
	Publish(payload []byte) error
}
