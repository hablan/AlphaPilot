from __future__ import annotations


def compact_error(exc: Exception, max_length: int = 500) -> str:
    """将异常对象压缩为单行可存储的错误信息。"""
    text = str(exc).replace("\n", " ").strip()
    return text[:max_length] if text else exc.__class__.__name__
