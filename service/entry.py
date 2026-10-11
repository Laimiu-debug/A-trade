"""Bounded JSON-line interface used only by the desktop's private child pipe."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from service.account_service import AccountService
from service.market_service import MarketService

MAX_LINE = 32 * 1024 * 1024


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    service = None
    startup_error = None
    market_service = None
    market_error = None
    research_service = None
    research_error = None
    mode = sys.argv[2] if len(sys.argv) > 2 else "all"
    try:
        if mode not in ("market", "research"):
            service = AccountService(Path(sys.argv[1]))
    except ValueError as error:
        startup_error = str(error)
    except Exception:
        startup_error = "账户数据库无法打开，原文件已保留。请检查目录权限、磁盘空间和备份。"
    try:
        if mode not in ("account", "research"):
            market_service = MarketService(Path(sys.argv[1]))
    except Exception:
        market_error = "行情缓存数据库无法打开，原文件已保留。请检查目录权限和磁盘空间；账户功能仍可正常使用。"
    try:
        if mode in ("all", "research"):
            from service.research_service import ResearchService
            research_service = ResearchService(Path(sys.argv[1]))
    except Exception:
        research_error = "研究服务无法启动，原研究记录已保留。请检查目录权限和磁盘空间。"
    try:
        while True:
            line = sys.stdin.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                break
            request_id = None
            try:
                request = json.loads(line)
                request_id = request["id"]
                if not isinstance(request_id, int) or not isinstance(request.get("payload", {}), dict):
                    raise ValueError("请求格式无效")
                method = request["method"]
                if not isinstance(method, str):
                    raise ValueError("请求格式无效")
                if method.startswith("research."):
                    if mode not in ("all", "research"):
                        raise ValueError("研究请求须通过研究服务")
                    if research_error:
                        raise ValueError(research_error)
                    data = research_service.dispatch(method, request.get("payload", {}))
                elif method.startswith("market."):
                    if mode not in ("all", "market"):
                        raise ValueError("行情请求须通过行情服务")
                    if market_error:
                        raise ValueError(market_error)
                    def report_progress(data):
                        sys.stdout.write(json.dumps({"id": request_id, "event": "progress", "data": data}, ensure_ascii=False, allow_nan=False) + "\n")
                    market_service.research.progress = report_progress
                    market_service.progress = lambda stage, completed, total: report_progress({
                        "stage": stage, "completed": completed, "total": total})
                    data = market_service.dispatch(method, request.get("payload", {}))
                else:
                    if mode not in ("all", "account"):
                        raise ValueError("账户请求须通过账户服务")
                    if startup_error:
                        raise ValueError(startup_error)
                    data = service.dispatch(method, request.get("payload", {}))
                response = {"id": request_id, "ok": True, "data": data}
            except ValueError as error:
                response = {"id": request_id, "ok": False, "error": str(error)}
            except Exception:
                # Never send raw SQL, file paths, imported content or tracebacks to the renderer.
                response = {"id": request_id, "ok": False, "error": "本地业务操作失败，原数据已保留。请检查数据格式、目录权限和磁盘空间。"}
            sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n")
            if startup_error and market_error:
                break
    finally:
        if service is not None:
            service.close()
        if market_service is not None:
            market_service.close()
        if research_service is not None:
            research_service.close()


if __name__ == "__main__":
    main()
