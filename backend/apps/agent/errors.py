class AgentRunError(RuntimeError):
    code = "AGENT_RUN_FAILED"


class RetryableAgentRunError(AgentRunError):
    code = "AGENT_RETRYABLE_FAILURE"


class AgentRunNotReady(AgentRunError):
    code = "CORPUS_NOT_READY"


class AgentRunCancelled(AgentRunError):
    code = "CANCELLED"
