"""Agg-only chart renderer; no model-created coordinates or numbers."""
from hashlib import sha256
from pathlib import Path
import os
import math
import textwrap

import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.ticker import FuncFormatter
import numpy as np

from .errors import AgentError

COLORS = ["#006B98", "#21A5C1", "#154B78", "#7DC9DA", "#A6CBDC", "#7797B3", "#C8E2EC", "#C8D2DA"]
STYLE_VERSION = 'manual-blue-2'


def chinese_font():
    candidates = [os.environ.get("ASSESS_CHART_FONT", ""), "C:/Windows/Fonts/msyh.ttc",
                  "C:/Windows/Fonts/simsun.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            font_manager.fontManager.addfont(candidate)
            return font_manager.FontProperties(fname=candidate)
    for family in ["Noto Sans CJK SC", "Microsoft YaHei", "SimSun", "WenQuanYi Zen Hei", "PingFang SC"]:
        path = font_manager.findfont(family, fallback_to_default=False) if any(f.name == family for f in font_manager.fontManager.ttflist) else ""
        if path:
            return font_manager.FontProperties(fname=path)
    raise AgentError("CHART_FONT", "缺少中文字体，请配置 ASSESS_CHART_FONT 后重试")


def fmt(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{value:,.0f}"


def axis_label(name, unit):
    return name if name.endswith((f"（{unit}）", f"({unit})")) else name + f"（{unit}）"


def value_label(value,unit):
    return fmt(value)+('%' if unit=='%' else '')


def response_grid(ax, figure, names, values, series, font):
    """Every segment stays readable, including small or zero responses."""
    table=ax.table(cellText=[[value_label(y[i],'%') for y in values] for i in range(len(names))],
        rowLabels=[str(i+1) for i in range(len(names))],colLabels=[s['name'] for s in series],
        cellLoc='center',bbox=[0,-.12-len(names)*.075,1,.1+len(names)*.075])
    table.auto_set_font_size(False);table.set_fontsize(9)
    for (r,c),cell in table.get_celld().items():
        cell.set_edgecolor('#C8D6DE');cell.set_linewidth(.4)
        cell.get_text().set_fontproperties(font)
        if r==0:cell.set_facecolor('#EDF5F8');cell.get_text().set_color(COLORS[0])
    figure.subplots_adjust(left=.32,right=.97,top=.85,bottom=.36)


def place_scatter_labels(ax,figure,names,values,font):
    """Choose nearby label positions against actual rendered bounding boxes."""
    figure.canvas.draw();renderer=figure.canvas.get_renderer()
    points=[ax.transData.transform((values[0][i],values[1][i])) for i in range(len(names))
            if np.isfinite(values[0][i]) and np.isfinite(values[1][i])]
    placed=[];overlaps=0
    for i in sorted(range(len(names)),key=lambda i:-len(names[i])):
        if not (np.isfinite(values[0][i]) and np.isfinite(values[1][i])):continue
        annotation=ax.annotate(textwrap.fill(names[i],14),(values[0][i],values[1][i]),
            xytext=(8,8),textcoords='offset points',fontsize=9,fontproperties=font,
            arrowprops={'arrowstyle':'-','color':'#96AEBB','lw':.5})
        candidates=[]
        for dy in (10,-10,28,-28,48,-48,70,-70,95,-95,125,-125):
            for dx in (8,-8,35,-35,65,-65,100,-100):
                annotation.set_position((dx,dy));annotation.set_ha('left' if dx>=0 else 'right')
                annotation.set_va('bottom' if dy>=0 else 'top')
                annotation.update_positions(renderer)
                # Text bounds only: including the arrow would count its own
                # anchor and inflate collisions for every proposed position.
                bbox=matplotlib.text.Text.get_window_extent(annotation,renderer).expanded(1.05,1.25)
                hits=sum(bbox.overlaps(b) for b in placed)
                hit_points=sum(bbox.x0-4<=x<=bbox.x1+4 and bbox.y0-4<=y<=bbox.y1+4 for x,y in points)
                outside=sum((bbox.x0<ax.bbox.x0,bbox.x1>ax.bbox.x1,bbox.y0<ax.bbox.y0,bbox.y1>ax.bbox.y1))
                candidates.append((hits*100000+hit_points*100000+outside*100000+dx*dx+dy*dy,dx,dy,bbox,hits))
        _,dx,dy,bbox,hits=min(candidates,key=lambda c:c[0])
        annotation.set_position((dx,dy));annotation.set_ha('left' if dx>=0 else 'right');annotation.set_va('bottom' if dy>=0 else 'top')
        placed.append(bbox);overlaps+=hits
    return int(overlaps)


def display_label(label):
    # Reader-facing names omit questionnaire navigation and example lists.
    # The exact source option remains intact in the dataset and audit.
    aliases = [('无工作，继续寻找工作', '继续寻找工作'), ('无工作，其他', '其他未就业'),
               ('准备到港澳台深造或国外留学', '准备境外深造'), ('准备中国境内考研', '准备境内考研'),
               ('有全职工作，自主创业', '自主创业'), ('自由职业（', '自由职业')]
    return next((name for prefix,name in aliases if label.startswith(prefix)),label)


def draw(data: dict, item: dict, directory: Path, asset_id: str) -> list[dict]:
    kind, labels, series = data["type"], data["labels"], data["series"]
    font = chinese_font()
    horizontal = kind in {"horizontal_bar", "stacked_bar", "mirror_bar"} or (kind=='bar' and len(labels)==1)
    chunk_size = 12 if horizontal else 10 if kind == "bar" else len(labels)
    if horizontal:
        longest=max(len(textwrap.wrap(n,20)) for n in labels)
        chunk_size=min(chunk_size,max(2,18//max(1,longest)))
    if kind=='stacked_bar':chunk_size=min(chunk_size,6)
    # Balance the final continuation rather than leaving one category on its own.
    chunk_size = math.ceil(len(labels) / math.ceil(len(labels) / chunk_size))
    files = []
    for page, offset in enumerate(range(0, len(labels), chunk_size), 1):
        names = [display_label(label) for label in labels[offset:offset + chunk_size]]
        values = [np.array([float("nan") if v is None else v for v in s["values"][offset:offset + chunk_size]]) for s in series]
        # Do not emit a continuation that contains only missing values; the data
        # and missing count remain in the provenance and document note.
        if not any(np.isfinite(v).any() for v in values): continue
        height = (1.5 if horizontal and len(names)==1 else max(2.6, sum(max(1,len(textwrap.wrap(n,20))) for n in names) * .3 + 1.1)) if horizontal else 3.8
        if kind=='stacked_bar':height+=1.2+len(names)*.12
        figure = Figure(figsize=(4.4, 3.7) if kind in ('pie','donut') else (8.6, min(height, 7)),
                        dpi=210 if kind in ('pie','donut') else 180, facecolor="white")
        FigureCanvasAgg(figure)
        with matplotlib.rc_context({"font.family": font.get_name(), "font.size": 10, "axes.unicode_minus": False}):
            ax = figure.add_subplot(111)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_visible(False)
            x = np.arange(len(names))
            if kind in {"pie", "donut"}:
                colors = COLORS[:len(names)] if len(names) <= len(COLORS) else matplotlib.colormaps["tab20"](np.linspace(0, 1, len(names)))
                if kind=='donut':colors=[COLORS[0],'white']
                ax.pie(values[0], labels=None if kind=='donut' else [textwrap.fill(n, 12)+'\n'+value_label(v,'%') for n,v in zip(names,values[0])], colors=colors,
                       startangle=90, counterclock=False,
                       wedgeprops={"width": 0.28 if kind == "donut" else 1, "edgecolor": "white"},
                       textprops={"fontproperties": font, "fontsize": 11})
                if kind == "donut":
                    for wedge in ax.patches:wedge.set_edgecolor(COLORS[0]);wedge.set_linewidth(1.2)
                    ax.text(0, .15, value_label(values[0][0],'%'), ha="center", va="center", fontproperties=font, fontsize=23, weight='bold',color=COLORS[0])
                    center={'figure-1-6':'参加研究生考试','figure-1-8':'获得工作录用通知'}.get(item.get('id'),names[0])
                    ax.text(0,-.2,textwrap.fill(center,8),ha='center',va='center',fontproperties=font,fontsize=12,color=COLORS[0])
                ax.set_aspect("equal")
            elif kind == "scatter":
                finite = np.isfinite(values[0]) & np.isfinite(values[1])
                if not finite.any():
                    raise AgentError("CHART_EMPTY", "散点图没有两个指标同时有效的样本")
                ax.scatter(values[0][finite], values[1][finite], s=55, color=COLORS[0])
                ax.axvline(values[0][finite].mean(), linestyle="--", color="#AAB7BF")
                ax.axhline(values[1][finite].mean(), linestyle="--", color="#AAB7BF")
                ax.set_xlabel(axis_label(series[0]["name"], data['unit']), fontproperties=font)
                ax.set_ylabel(axis_label(series[1]["name"], data['unit']), fontproperties=font)
                ax.margins(0.25)
            elif kind == "line":
                for j, y in enumerate(values):
                    ax.plot(x, y, marker="o", markersize=6, linewidth=2, color=COLORS[j], label=series[j]["name"])
                    for i, v in enumerate(y):
                        if np.isfinite(v):
                            if len(values)<=2:ax.annotate(value_label(v,data['unit']), (x[i], v), xytext=(0, 8 + j * 8), textcoords="offset points", ha="center", color=COLORS[j], fontproperties=font, fontsize=10)
                ax.set_xticks(x, names)
                ax.grid(axis="y", color='#DDE6EB',linewidth=.5,alpha=.5)
                ax.set_ylabel(data["unit"], fontproperties=font)
                ax.margins(y=0.24)
                # Small data grid below the curve, including explicit gaps.
                table = ax.table(cellText=[["—" if not np.isfinite(v) else fmt(v) for v in y] for y in values],
                                 rowLabels=[s["name"] for s in series], colLabels=names, cellLoc="center", bbox=[0, -0.38, 1, 0.24])
                table.auto_set_font_size(False)
                table.set_fontsize(9)
                for cell in table.get_celld().values():
                    cell.set_edgecolor("#D5E0E6")
                    cell.get_text().set_fontproperties(font)
                figure.subplots_adjust(bottom=0.34)
            elif kind == "stacked_bar":
                left = np.zeros(len(names))
                for j, y in enumerate(values):
                    ax.barh(x, y, left=left, color=COLORS[j], label=series[j]["name"], height=0.65)
                    for i, v in enumerate(y):
                        if v >= 6:
                            ax.text(left[i] + v / 2, i, value_label(v,'%'), ha="center", va="center", fontsize=10, color="white" if j <= 2 else "#16323F", fontproperties=font)
                    left += y
                ax.set_xlim(0, 100)
                ax.set_yticks(x, [str(i+1)+'. '+textwrap.fill(n, 22) for i,n in enumerate(names)])
                ax.invert_yaxis()
                ax.set_xticks([])
                response_grid(ax,figure,names,values,series,font)
            elif kind == "mirror_bar":
                ax.barh(x, -values[0], height=0.58, color=COLORS[0], label=series[0]["name"])
                ax.barh(x, values[1], height=0.58, color=COLORS[1], label=series[1]["name"])
                for j, y in enumerate(values):
                    for i, v in enumerate(y):
                        if np.isfinite(v):
                            ax.annotate(value_label(v,data['unit']), ((-1 if j == 0 else 1) * v, i), xytext=(-5 if j == 0 else 5, 0), textcoords="offset points", ha="right" if j == 0 else "left", va="center", fontsize=10, color=COLORS[j],fontproperties=font)
                ax.set_yticks(x, [textwrap.fill(n, 20) for n in names])
                ax.invert_yaxis()
                ax.axvline(0, color="#AAB7BF", linewidth=0.7)
                ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt(abs(v))))
                ax.margins(x=0.18)
                ax.set_xlabel(data["unit"], fontproperties=font)
            else:
                width = 0.72 / len(values)
                for j, y in enumerate(values):
                    pos = x + (j - (len(values) - 1) / 2) * width
                    if horizontal:
                        ax.barh(pos, y, height=width, color=COLORS[j], label=series[j]["name"])
                    else:
                        ax.bar(pos, y, width=width, color=COLORS[j], label=series[j]["name"])
                    for i, v in enumerate(y):
                        if not np.isfinite(v):
                            if len(values) == 1:
                                ax.text(0, pos[i], "缺失", va="center", fontsize=9, color="#74818A", fontproperties=font) if horizontal else ax.text(pos[i], 0, "缺失", ha="center", fontsize=9, color="#74818A", fontproperties=font)
                            continue
                        ax.annotate(value_label(v,data['unit']), (v, pos[i]) if horizontal else (pos[i], v), xytext=(5, 0) if horizontal else (0, 4), textcoords="offset points", ha="left" if horizontal else "center", va="center" if horizontal else "bottom", fontsize=11, color=COLORS[j],weight='bold',fontproperties=font)
                if horizontal:
                    ax.set_yticks(x, [textwrap.fill(n, 20) for n in names])
                    ax.invert_yaxis()
                    ax.set_xlabel(data["unit"], fontproperties=font)
                    ax.set_xticks([])
                    ax.margins(x=0.16)
                else:
                    ax.set_xticks(x, [textwrap.fill(n, 8) for n in names])
                    if len(names) == 1:
                        # One available cohort remains a bar, with room around it
                        # rather than a rectangle filling almost the entire plot.
                        ax.set_xlim(-1, 1)
                    ax.set_ylabel(data["unit"], fontproperties=font)
                    ax.grid(axis="y", color='#DDE6EB',linewidth=.5,alpha=.5)
                    ax.margins(y=0.2)
            benchmark = data.get("benchmarks", [])
            has_benchmark = len(series) == 1 and kind in {"horizontal_bar", "bar"} and bool(benchmark) and benchmark[0] is not None
            if kind in {"horizontal_bar", "bar"}:
                actual_maximum = max([float(v) for y in values for v in y if np.isfinite(v)] + ([benchmark[0]] if has_benchmark else []) + [1])
                maximum = 114 if kind == 'bar' and len(names) == 1 and data['unit'] == '%' else actual_maximum * 1.18
                (ax.set_xlim if horizontal else ax.set_ylim)(0, maximum)
            if has_benchmark:
                baseline = benchmark[0]
                label = "本校平均 " + fmt(baseline) + data["unit"]
                (ax.axvline if horizontal else ax.axhline)(baseline, color="#658190", linestyle="--", linewidth=1.2, label=label)
            if (len(series) > 1 or has_benchmark or item.get('id')=='figure-5-29') and kind != "scatter":
                ax.legend(prop=font, loc="lower center", bbox_to_anchor=(0.5, 1.03), ncol=min(4, len(series)), frameon=False, fontsize=9)
            if kind not in {"line","stacked_bar"}:
                figure.tight_layout(pad=1.4)
            label_overlaps=place_scatter_labels(ax,figure,names,values,font) if kind=='scatter' else 0
            path = directory / f"{asset_id}-{page}.png"
            figure.savefig(path, dpi=180, bbox_inches="tight", facecolor="white", metadata={"Software": "AssessReport"})
            files.append({"filename": path.name, "sha256": sha256(path.read_bytes()).hexdigest(), "width": float(figure.get_figwidth()), "height": float(figure.get_figheight()), "page": page,'styleVersion':STYLE_VERSION,'labelOverlaps':label_overlaps})
            figure.clear()
    return files
