"""English catalog for local KataGo tuning; Chinese source strings are the default UI language."""
from __future__ import annotations

from .i18n import get_language

ENGLISH = {
    "调优前请停止对局搜索或分析。测试有时间限制，不保证性能提升。":
        "Stop game searches or analysis before tuning. Tests are time-limited and performance improvements are not guaranteed.",
    "开始调优": "Start tuning",
    "取消调优": "Cancel tuning",
    "恢复上次配置": "Restore previous configuration",
    "已请求取消，正在清理引擎进程…": "Cancellation requested; cleaning up engine processes…",
    "内存不足": "Out of memory",
    "缺少引擎运行库或 DLL": "Missing engine runtime or DLL",
    "后端或设备不可用": "Backend or device unavailable",
    "引擎失败（配置、模型或进程错误）": "Engine failed (configuration, model, or process error)",
    "{0}；退出码 {1}。{2}": "{0}; exit code {1}. {2}",
    "配置必须且只能包含一个显式整数 numSearchThreads 设置。":
        "The configuration must contain exactly one explicit integer numSearchThreads setting.",
    "基准测试没有生成完整且可解析的 visits/s 结果。": "The benchmark produced no complete, parseable visits/s result.",
    "基准测试返回了无效的 visits/s 结果。": "The benchmark returned an invalid visits/s result.",
    "已过期，请重新测试": "outdated; please retest",
    "文件和环境标识一致": "file and environment identity unchanged",
    "最近测试：{0}，{1:.2f} → {2:.2f} visits/s（{3}）。GPU 或设备变化可能需要重新测试。日志：{4}":
        "Last measurement: {0}, {1:.2f} → {2:.2f} visits/s ({3}). GPU or device changes may require retesting. Log: {4}",
    "暂无最近调优测试结果。": "No recent tuning measurement.",
    "调优已取消，原配置保持不变。": "Tuning cancelled; the original configuration was kept.",
    "调优超过总时限，原配置保持不变。": "Tuning exceeded its total time limit; the original configuration was kept.",
    "命令：{0}": "Command: {0}",
    "GTP 输入：\n{0}": "GTP input:\n{0}",
    "引擎命令超时，原配置保持不变。": "Engine command timed out; the original configuration was kept.",
    "引擎不可用或无法启动：{0}": "Engine unavailable or could not start: {0}",
    "配置 GTP 自检返回无效响应：{0}": "Configuration GTP self-check returned invalid responses: {0}",
    "配置 GTP 自检失败或响应不完整。": "Configuration GTP self-check failed or returned incomplete responses.",
    "配置 GTP 自检返回无效着手。": "Configuration GTP self-check returned an invalid move.",
    "引擎文件不可用：{0}": "Engine files unavailable: {0}",
    "基准测试仅支持 7–19 路棋盘。": "The benchmark supports board sizes 7–19 only.",
    "当前 numSearchThreads 超出安全测试范围（1–256）。":
        "The current numSearchThreads is outside the safe test range (1–256).",
    "正在检查引擎版本和支持的基准测试选项。": "Checking engine version and supported benchmark options.",
    "不支持或无法识别的 KataGo 后端，未更改设置。": "Unsupported or unrecognized KataGo backend; settings were not changed.",
    "该引擎未声明支持 benchmark。": "This engine does not advertise benchmark support.",
    "引擎基准测试选项不兼容，未更改设置。": "Engine benchmark options are incompatible; settings were not changed.",
    "后端：{0}；棋盘：{1}；模型：{2}；预热后采样 {3} 次，每次 {4} visits。":
        "Backend: {0}; board: {1}; model: {2}; warmup followed by {3} samples, {4} visits each.",
    "预热": "warmup",
    "采样 {0}/{1}": "sample {0}/{1}",
    "线程 {0}：{1}（已用 {2:.1f} 秒）": "Threads {0}: {1} ({2:.1f}s elapsed)",
    "实测 {0:.2f} visits/s": "Measured {0:.2f} visits/s",
    "调优前中位数：{0:.2f} visits/s；最佳：{1:.2f} visits/s（{2} 线程）。不保证性能提升。":
        "Median before tuning: {0:.2f} visits/s; best: {1:.2f} visits/s ({2} threads). Improvement is not guaranteed.",
    "调优期间引擎、模型或配置已变化，不应用结果。":
        "The engine, model, or configuration changed during tuning; the result was not applied.",
    "没有显著收益（至少 5%），原配置保持不变。":
        "No significant gain (at least 5%); the original configuration was kept.",
    "正在用 GTP 和短搜索验证配置。": "Verifying the configuration with GTP and a short search.",
    "无法保存最近测试结果：{0}": "Could not save the recent measurement: {0}",
    "已验证配置：{0}；备份：{1}": "Verified configuration: {0}; backup: {1}",
    "调优失败：{0}": "Tuning failed: {0}",
    "总用时：{0:.1f} 秒": "Elapsed: {0:.1f}s",
    "调优日志：{0}": "Tuning log: {0}",
    "无法写入调优日志：{0}": "Could not write the tuning log: {0}",
    "备份路径或内容无效": "Invalid backup path or contents",
    "正在用 GTP 和短搜索验证上次配置。": "Verifying the previous configuration with GTP and a short search.",
    "上次配置已通过验证：{0}": "Previous configuration verified: {0}",
    "上次配置不可用：{0}": "Previous configuration unavailable: {0}",
    "调优线程意外失败：{0}": "Tuning worker failed unexpectedly: {0}",
}


def tt(source: str, *args: object) -> str:
    text = ENGLISH.get(source, source) if get_language() == "en" else source
    return text.format(*args) if args else text
