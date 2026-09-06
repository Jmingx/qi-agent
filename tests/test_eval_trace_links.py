"""评测输出中的 Jaeger Trace 链接测试。"""

from evaluation.gateway_runner import _jaeger_url


def test_build_jaeger_trace_url() -> None:
    assert (
        _jaeger_url("trace-1", "http://127.0.0.1:16686/jaeger/")
        == "http://127.0.0.1:16686/jaeger/trace/trace-1"
    )


def test_build_jaeger_trace_url_without_trace() -> None:
    assert _jaeger_url("", "http://jaeger/jaeger") == "-"
