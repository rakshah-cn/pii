"""LLM dispatch: the ONLY place text leaves the gateway for a model, and it only ever receives audited, tokenised text.
The call runs in a worker thread with a hard timeout; a timeout raises LLMTimeout (the gateway answers with a generic degraded reply, it does not block the conversation).
Any other failure is an error (the gateway blocks: nothing is sent). make_bedrock_llm is a thin adapter around the Bedrock Converse API; the client is injectable so it can be
tested without a network. NOT exercised against the real service in this repo's tests."""
import concurrent.futures
import threading

DEFAULT_TIMEOUT = 20.0
MAX_ABANDONED = 32            # calls that timed out and are still running: past this the model is treated as down (degraded reply at once) instead of starting more threads
_ABANDONED, _LOCK = [], threading.Lock()
DEGRADED_REPLY = "We are unable to process this right now. Please try again in a moment."


class LLMTimeout(Exception):
    pass


def dispatch(llm, masked_text, timeout=DEFAULT_TIMEOUT, max_abandoned=MAX_ABANDONED):
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("llm_timeout_must_be_positive")                    # no 'wait forever'
    with _LOCK:
        _ABANDONED[:] = [f for f in _ABANDONED if not f.done()]
        if len(_ABANDONED) >= max_abandoned:
            raise LLMTimeout()                                              # the model looks hung: no new worker thread
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(llm, masked_text)
    pool.shutdown(wait=False)
    try:
        out = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        with _LOCK:
            _ABANDONED.append(fut)
        raise LLMTimeout()
    if not isinstance(out, str):
        raise TypeError("llm_returned_non_text")
    return out


def make_bedrock_llm(model_id, client=None, region=None, system="You are a helpful loan-servicing assistant. Placeholders like <PHONE_1> stand for private values; keep them as they are.", max_tokens=300):
    def call(masked_text):
        c = client
        if c is None:
            import boto3
            c = boto3.client("bedrock-runtime", region_name=region)
        r = c.converse(modelId=model_id, system=[{"text": system}], messages=[{"role": "user", "content": [{"text": masked_text}]}], inferenceConfig={"maxTokens": max_tokens})
        return r["output"]["message"]["content"][0]["text"]
    return call
