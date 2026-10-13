"""运行长任务时阻止 Windows 进入睡眠。

平原回测、普通回测、交叉验证等后台任务可能跑很久，期间若用户未操作电脑，
Windows 会在几分钟后进入睡眠并把整个进程挂起，任务线程被冻结，醒来才继续。

本模块封装 Windows API ``SetThreadExecutionState``：在任务线程进入时申请
``ES_SYSTEM_REQUIRED``（告诉系统「我很忙，别睡」），任务结束（正常返回或异常）
时清除该状态，恢复系统默认的空闲策略。

注意：
- 仅 Windows 生效，其它平台为 no-op，不影响跨平台运行。
- 该 API 是线程级的，必须在执行任务的**后台线程**内调用，所以用 ``keep_awake_while``
  装饰传给 ``Thread(target=...)`` 的可调用对象，而不是在主线程调用。
- 它只能阻止「空闲睡眠」，无法阻止电池耗尽强制休眠或合盖休眠。
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable, TypeVar

T = TypeVar("T")

if sys.platform == "win32":  # pragma: no cover - 平台分支
    try:
        import ctypes

        _ES_CONTINUOUS = 0x80000000
        _ES_SYSTEM_REQUIRED = 0x00000001
        # 单独 ES_CONTINUOUS 表示清除之前设置的执行状态。
        _SET_ON = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        _SET_OFF = _ES_CONTINUOUS

        _set_thread_execution_state = ctypes.windll.kernel32.SetThreadExecutionState
        # winapi 函数签名：DWORD SetThreadExecutionState(DWORD esFlags)
        _set_thread_execution_state.restype = ctypes.c_ulong  # type: ignore[attr-defined]
        _set_thread_execution_state.argtypes = [ctypes.c_ulong]  # type: ignore[attr-defined]
        _AVAILABLE = True
    except Exception:  # noqa: BLE001 - 任何 ctypes 失败都降级为 no-op
        _AVAILABLE = False
else:
    _AVAILABLE = False


class KeepAwake:
    """上下文管理器：进入时阻止系统睡眠，退出时恢复。

    用法::

        with KeepAwake():
            run_long_task()
    """

    __slots__ = ()

    def __enter__(self) -> "KeepAwake":
        self.enable()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.disable()
        return False

    @staticmethod
    def enable() -> None:
        if _AVAILABLE:
            _set_thread_execution_state(_SET_ON)

    @staticmethod
    def disable() -> None:
        if _AVAILABLE:
            _set_thread_execution_state(_SET_OFF)


def keep_awake_while(fn: Callable[..., T]) -> Callable[..., T]:
    """装饰器：让被装饰函数运行期间 Windows 不进入睡眠。

    主要用于包装传给 ``Thread(target=...)`` 的后台 worker，使其在后台线程
    内申请/释放执行状态。
    """

    @functools.wraps(fn)
    def _wrapped(*args: Any, **kwargs: Any) -> T:
        with KeepAwake():
            return fn(*args, **kwargs)

    return _wrapped
