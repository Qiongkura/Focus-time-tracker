"""生成统计图表（今日 / 近 N 天）。"""
from __future__ import annotations

from datetime import datetime, timedelta

import matplotlib.pyplot as plt
from matplotlib import font_manager

from .db import UsageDB
from .utils import fmt_duration

_CJK_FONTS = [
    "Microsoft YaHei",
    "SimHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
]


def setup_cjk_font():
    """设置中文字体，返回实际使用的字体名。"""
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_FONTS:
        if name in installed:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    return None


def _empty_axes_message(ax, message: str):
    ax.axis("off")
    ax.text(0.5, 0.5, message, ha="center", va="center", transform=ax.transAxes)


def generate_today_chart(db: UsageDB, output_path, top_n: int = 10):
    """今日 Top 应用条形图 + 占比饼图。"""
    setup_cjk_font()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = datetime.now()
    data = db.summary_between(start, end)
    total = sum(d["seconds"] for d in data)

    fig, (ax_bar, ax_pie) = plt.subplots(1, 2, figsize=(13, 6.2))
    if not data:
        _empty_axes_message(ax_bar, "暂无数据\n请运行 python main.py start 开始记录")
        _empty_axes_message(ax_pie, "正在等待数据...")
    else:
        shown = data[:top_n]
        names = [d["process"] for d in shown][::-1]
        seconds = [d["seconds"] for d in shown][::-1]
        max_sec = max(seconds)
        bars = ax_bar.barh(names, seconds, color=plt.cm.tab10(range(len(shown))))
        for bar, sec in zip(bars, seconds):
            ax_bar.text(
                bar.get_width() + max_sec * 0.01, bar.get_y() + bar.get_height() / 2,
                fmt_duration(sec), va="center", fontsize=9,
            )
        ax_bar.set_title(f"今日使用时长 Top{min(top_n, len(shown))}")
        ax_bar.set_xlabel("时长")
        ax_bar.set_xlim(0, max_sec * 1.22)

        pie = data[:8]
        labels = [d["process"] for d in pie]
        sizes = [d["seconds"] for d in pie]
        rest = total - sum(sizes)
        if rest > 0.5:
            labels.append("其他")
            sizes.append(rest)
        ax_pie.pie(sizes, labels=labels, autopct=lambda p: f"{p:.0f}%" if p >= 4 else "", startangle=90)
        ax_pie.set_title(f"今日占比 · 总计 {fmt_duration(total)}")

    fig.suptitle(f"屏幕使用时间统计 · {start:%Y年%m月%d日}", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def generate_week_chart(db: UsageDB, output_path, days: int = 7, top_n: int = 6):
    """近 N 天按应用的堆叠柱状趋势图。"""
    setup_cjk_font()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    end = datetime.now()
    data = db.summary_between(start, end)
    breakdown = db.daily_breakdown(days)

    fig, ax = plt.subplots(figsize=(12, 6.2))
    if not data:
        _empty_axes_message(ax, "暂无数据\n请运行 python main.py start 开始记录")
    else:
        top = [d["process"] for d in data[:top_n]]
        dates = [(start + timedelta(days=i)).strftime("%m-%d") for i in range(days)]
        bottom = [0.0] * days
        for proc in top:
            vals = []
            for i in range(days):
                day_key = (start + timedelta(days=i)).strftime("%Y-%m-%d")
                vals.append(breakdown.get(day_key, {}).get(proc, 0.0) / 3600.0)
            ax.bar(dates, vals, bottom=bottom, label=proc, width=0.62)
            bottom = [b + v for b, v in zip(bottom, vals)]

        day_totals, others_vals = [], []
        for i in range(days):
            day_key = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            day_total = sum(breakdown.get(day_key, {}).values()) / 3600.0
            day_totals.append(day_total)
            others_vals.append(max(0.0, day_total - bottom[i]))
        if any(v > 0.05 for v in others_vals):
            ax.bar(dates, others_vals, bottom=bottom, label="其他", width=0.62, color="#bdbdbd")
            bottom = [b + v for b, v in zip(bottom, others_vals)]
        for i, total in enumerate(day_totals):
            if total > 0:
                ax.text(i, total + 0.25, f"{total:.1f}h", ha="center", fontsize=9)
        ax.set_ylabel("小时")
        ax.set_title(f"近 {days} 天使用趋势（按应用）")
        ax.legend(ncol=min(top_n + 1, 4), fontsize=9, loc="upper left")
        ax.set_ylim(0, (max(day_totals) + 2.0) if day_totals else 2.0)

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path
