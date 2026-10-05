"""Conservative parsing of explicit WORK locations, never student origins."""
import re
from collections import Counter, defaultdict

from .survey import present, percentage

PROVINCES = ('北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
    '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南', '广东',
    '海南', '四川', '贵州', '云南', '陕西', '甘肃', '青海', '内蒙古', '广西', '西藏',
    '宁夏', '新疆', '香港', '澳门', '台湾')
MUNICIPALITIES = {'北京', '天津', '上海', '重庆'}


def location_parts(value):
    value = value.strip()
    province = next((p for p in sorted(PROVINCES, key=len, reverse=True) if value.startswith(p)), '')
    if not province:
        return '', ''
    if province in MUNICIPALITIES:
        return province, province
    suffix = value[len(province):]
    suffix = re.sub(r'^(?:维吾尔自治区|回族自治区|壮族自治区|自治区|特别行政区|省)', '', suffix)
    # Only parse a city when the uploaded address explicitly uses 市. Shortened
    # place strings need an uploaded codebook; county/district names are not cities.
    match = re.match(r'^([^市省区县]{1,12})市', suffix)
    return province, match.group(1) if match else ''


def plan_regions(registry, table, rows, location_index, industry_index, career_index, major_label):
    from .report_statistics import register
    located = [(r, *location_parts(r[location_index])) for r in rows if present(r[location_index])]
    if not located:
        return
    for key, index, label in [('table-1-1', 1, '就业地区'), ('table-1-2', 2, '就业城市')]:
        counts = Counter(item[index] or '原始地点尚不能分类' for item in located)
        output = [[name, str(count), str(percentage(count, len(located)))] for name, count in counts.most_common()]
        register(registry, key, table, output, [label, '有效回答人数', '比例（%）'],
            f'{registry.year}届就业样本；只采用工作单位所在地，以该字段有效回答为分母。'
            '按明确省份前缀或市名分类，地址省略城市名时不猜测；未分类项计入分母。',valid=len(located))
    # For these municipality-named schools the city is explicit in the school
    # name. Other locations need an explicit school city, not model geography.
    school = getattr(registry.evidence.survey, 'school', '')
    city = next((p for p in MUNICIPALITIES if school.startswith(p)), '')
    if city:
        local = [r for r, _, c in located if c == city]
        for key, ci, label in [('table-1-3', industry_index, '行业类'), ('table-1-4', career_index, '职业类')]:
            if ci is None:
                continue
            counts = Counter(r[ci] for r in local if present(r[ci]))
            total = sum(counts.values())
            register(registry, key, table, [[name, str(n), str(percentage(n, total))] for name,n in counts.most_common()],
                [label, '有效回答人数', '比例（%）'],
                f'{registry.year}届；工作所在地为{city}的就业样本，以该群体行业或职业有效回答为分母。',valid=total)
    if major_label is not None:
        groups = defaultdict(list)
        for row, province, _ in located:
            label=major_label(row)
            if label:
                groups[label].append(province or '原始地点尚不能分类')
        output = []
        for major, values in sorted(groups.items()):
            if len(values) >= 5:
                output.append([major, '；'.join(f'{p}（{percentage(n,len(values)):g}%）' for p,n in Counter(values).most_common(3))])
        register(registry, 'table-7-12', table, output, ['专业', '主要地区就业比例'],
            f'{registry.year}届就业样本；按上传资料标注的一流专业分组，以各专业工作所在地有效回答为分母，列前三地区；少于5人不展示。')
