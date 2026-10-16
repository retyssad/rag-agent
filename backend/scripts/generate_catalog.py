"""按数量生成"各不相同、明确标记"的虚构演示教练与知识文档。

与 generate_demo.py 的区别：面向较大规模目录（默认 50 位），仍遵守同一
导入契约（见 docs/DATA_GUIDE.md）；且**不修改** data/demo/dataset.json，
那个文件是测试与固定评估使用的 fixture。

用法：
    python -m scripts.generate_catalog --count 50 --output data/demo/catalog-50.json

确定性：同一 seed 与 count 产生完全相同的结果，便于复现与重复导入（幂等）。
"""
import argparse
import json
import random
from pathlib import Path

# 90 个不重复的虚构中文名；count + offset 上限为名字池长度。
NAMES = [
    '白慕言', '崔子恒', '邓清越', '范书宁', '高允泽', '何蔓仪', '侯继文', '胡以宁', '黄立诚', '纪悠然',
    '贾斯远', '柯霁月', '雷亭舟', '黎昀希', '李维朗', '梁小满', '林肃之', '刘星遥', '卢知微', '罗启年',
    '马予棠', '孟修竹', '倪昭华', '潘叙白', '彭之澜', '齐璟然', '秦望舒', '邱彦舟', '曲依然', '任向晚',
    '商知白', '邵禾风', '申屠衡', '石萦之', '舒墨言', '司徒南', '孙未眠', '谭砚舟', '汤予初', '陶立群',
    '万清和', '汪听雪', '王砚秋', '韦念初', '文景衍', '吴栖梧', '伍清泽', '席临川', '夏木西', '向知遥',
    '萧岸芷', '谢朗然', '邢鹤鸣', '徐砚池', '薛静姝', '鄢鹤川', '杨照野', '姚未晞', '殷书鹤', '尹栖迟',
    '应清野', '游云归', '于听澜', '余鹤汀', '俞见山', '郁南枝', '袁行舟', '岳初棠', '曾照雪', '翟清弦',
    '詹叙川', '张羡之', '章梧声', '赵栖迟', '郑听寒', '钟离澈', '周慕辰', '朱映真', '祝清越', '庄砚白',
    '卓向南', '宗意宁', '邹时眠', '祖闻笛', '安既明', '柏知寒', '边望舒', '蔡斯年', '曹栖梧', '岑清让',
]
DISTRICTS = ['南山区', '福田区', '宝安区', '罗湖区', '龙岗区', '龙华区', '盐田区', '光明区']
MODES = [['线下'], ['线下', '线上'], ['线上'], ['线下', '线上']]
# 第一个专长是训练目标方向；覆盖 demo 路由可识别的常见目标词。
GOALS = ['减脂', '增肌', '力量', '体能', '体态', '塑形', '产后恢复', '青少年体能',
         '中老年体适能', '跑步', '普拉提', '搏击', '瑜伽', '游泳体能', '柔韧与体态调整', '运动表现提升']
SKILLS = ['新手入门', '进阶提升', '动作纠正', '力量基础', '周期化训练', '女性专属',
          '办公室人群', '久坐改善', '损伤后体能恢复', '体能测试与评估']
# 档期：days 为 0=周一…6=周日，整点 start<end（香港时区）。
WINDOWS = [
    ([0, 1, 2, 3, 4], 18, 22), ([5, 6], 9, 18), ([0, 1, 2, 3, 4], 9, 17), ([0, 2, 4], 19, 22),
    ([1, 3, 5], 18, 21), ([5, 6], 18, 21), ([0, 1, 2, 3, 4], 6, 9), ([0, 1, 2, 3, 4], 11, 14),
    ([0, 1, 2, 3, 4, 5, 6], 8, 20), ([6], 10, 16), ([0, 3, 5], 17, 21), ([2, 4], 20, 22),
    ([1, 2, 3], 14, 18), ([5], 9, 15), ([0, 1, 4], 7, 10), ([2, 5, 6], 13, 19),
]
WEEKDAY_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
# 平台通用资料：保留原 ID 以便评估集继续引用；仅首批生成，追加批次不重复输出。
PLATFORM_DOCS = [
    ('platform-fat-loss-course', '平台减脂基础课程（演示）',
     '# 减脂课程安排\n\n平台演示减脂课程按四周安排：第一周了解基础动作与训练记录；第二周加入全身力量训练；'
     '第三周根据反馈增加适量有氧；第四周复盘出勤与动作表现。每周建议两次私教训练。\n\n'
     '# 个体调整\n\n课程安排是演示模板，训练频次和强度需根据个人基础与恢复情况调整，不承诺减重幅度。'),
    ('platform-overload', '渐进超负荷学习笔记（演示）',
     '# 渐进超负荷\n\n渐进超负荷是随身体适应逐渐增加训练刺激。可通过负重、重复次数、组数或动作控制调整。'
     '一次优先改变一个变量。\n\n# 恢复与记录\n\n先记录完成质量和疲劳感，再决定下一次变化。'),
    ('platform-beginner', '新手训练说明（演示）',
     '# 新手入门\n\n新手可先从每周两次全身力量训练开始，动作包括推、拉、下肢与躯干稳定。'
     '先掌握动作质量再增加负荷，训练之间留出恢复时间。'),
    ('platform-safety', '运动不适处理原则（演示）',
     '# 运动不适\n\n训练中出现明显疼痛时应停止引发疼痛的动作。疼痛持续或反复时先寻求医疗评估。'
     '教练训练经验与医疗诊断、康复治疗资质是不同事实。'),
]


# 首批（offset=0）精确使用原手挑集合，保证 catalog-50.json 与既有 50 位完全可复现。
FIRST_NO_DOCS = {7, 23, 41, 48}
FIRST_TEACHING_ONLY = {5, 13, 29, 37, 46}


def data_tiers(count, offset=0):
    """给出"资料不足"的批内相对序号（1-based）。

    offset=0 的首批沿用原手挑集合，保证已有数据的可复现性；
    追加批次按比例生成，使小批次也有"资料不足"样本（约每 12 位一个无文档、
    每 10 位一个仅教学资料）。
    """
    if count <= 0:
        return set(), set()
    if offset == 0 and count >= 49:
        return {n for n in FIRST_NO_DOCS if n <= count}, {n for n in FIRST_TEACHING_ONLY if n <= count}
    no_docs = {rel for rel in range(7, count + 1, 12)}
    teaching_only = {rel for rel in range(5, count + 1, 10)}
    return no_docs, teaching_only


def window_text(days, start, end):
    if days == [0, 1, 2, 3, 4]:
        name = '工作日'
    elif days == [5, 6]:
        name = '周末'
    elif days == [0, 1, 2, 3, 4, 5, 6]:
        name = '每天'
    else:
        name = '、'.join(WEEKDAY_LABELS[d] for d in days)
    return f'{name} {start}:00–{end}:00'


def generate(count=50, seed=20261015, offset=0, id_start=1, price_shift=0, include_platform=True):
    """生成互不相同的虚构教练与文档。

    offset / id_start 用于**追加**：已有 N 位时，用 offset=N、id_start=N+1
    生成下一段，不会与已有 ID 或姓名重复。
    price_shift 让每批价格落在同一现实区间（150–650）内的不同网格上，
    例如第一批 shift=0（150,160,…），第二批 shift=5（155,165,…），两批不撞价。
    """
    if count < 1 or offset < 0 or offset + count > len(NAMES):
        raise ValueError(f'offset+count 必须在 1..{len(NAMES)} 之间')
    rng = random.Random(seed)
    pool = list(range(150 + price_shift, 651, 10))
    if len(pool) < count:
        raise ValueError(f'价格池不足：需要 {count} 个，只有 {len(pool)} 个')
    prices = rng.sample(pool, count)
    no_docs_rel, teaching_only_rel = data_tiers(count, offset)
    coaches, documents = [], []
    for slot in range(offset, offset + count):
        i = slot + 1
        # 批内相对序号（1-based）：NO_DOCS/TEACHING_ONLY 按此判断，
        # 使每个批次都有"资料不足"的样本；对首批 offset=0 时 rel == i，行为不变。
        rel = slot - offset + 1
        # 用互质的乘数打散各维度，使同一批次内两两组合互不相同。
        goal = GOALS[(i - 1) % len(GOALS)]
        skill = SKILLS[(i * 3 - 3) % len(SKILLS)]
        district = DISTRICTS[(i * 5 - 5) % len(DISTRICTS)]
        modes = MODES[(i * 3 - 3) % len(MODES)]
        days, start, end = WINDOWS[(i * 7 - 7) % len(WINDOWS)]
        name = NAMES[slot]
        coach_id = f'coach-{id_start + (slot - offset):02d}'
        price = prices[slot - offset]
        patient = i % 3 != 0
        knee = i % 4 == 0
        specialties = list(dict.fromkeys([goal, skill]))
        courses = [{'name': f'{goal}私教课', 'duration_minutes': 60, 'price': price}]
        if i % 5 == 0:
            courses.append({'name': f'{goal}进阶课', 'duration_minutes': 90, 'price': price + 30})
        coaches.append({
            'id': coach_id, 'name': name, 'price': price, 'districts': [district],
            'delivery_modes': modes, 'specialties': specialties,
            'qualifications': [{'title': '演示训练指导证书（虚构）', 'verified': False, 'scope': '非医疗资质'}],
            'courses': courses,
            'availability': [{'days': days, 'start': start, 'end': end}],
            'bio': f'{name} 是一位用于演示选择流程的虚构教练，主要面向{goal}方向，'
                   f'在{district}提供{"、".join(modes)}授课。资料为虚构样例，不代表真实资质。',
            'is_demo': True,
        })
        if rel in no_docs_rel:
            continue  # 明确覆盖"资料不足"：无任何文档。
        when = window_text(days, start, end)
        teaching = (f'{name}面向{goal}目标，在{district}以{"、".join(modes)}方式提供循序渐进的基础训练，'
                    f'常安排时间为{when}。教学上重视{skill}，先讲解再示范，并按训练反馈调整强度。')
        teaching += ('对新手耐心，习惯逐步解释动作，允许学员重复提问。' if patient else
                     '偏好用训练记录跟踪负重与次数，强调每周复盘。对沟通耐心程度，现有资料没有说明。')
        documents.append({
            'id': f'{coach_id}-teaching', 'coach_id': coach_id, 'title': f'{name}教学资料（虚构）',
            'source': f'demo://coaches/{coach_id}/teaching', 'document_type': 'teaching', 'version': '2',
            'content': '# 教学方式\n\n' + teaching, 'is_demo': True,
        })
        if rel in teaching_only_rel:
            continue  # 只有教学资料，缺训练案例。
        case = (f'一份虚构的{goal}教学案例记录：学员先学习基础动作，再由{name}根据训练反馈逐步调整组数与强度。'
                '案例只用于演示资料引用，不代表真实效果，也不能推算成功率。')
        if knee:
            case += (f'在另一个虚构案例中，{name}陪同一位自述膝盖不适且已获得医生运动许可的学员，'
                     '使用低冲击活动、控制动作幅度，并记录不适反馈。这是训练调整记录，不是医疗诊断或康复资质。')
        documents.append({
            'id': f'{coach_id}-case', 'coach_id': coach_id, 'title': f'{name}训练案例（虚构）',
            'source': f'demo://coaches/{coach_id}/case', 'document_type': 'case', 'version': '2',
            'content': '# 训练案例\n\n' + case, 'is_demo': True,
        })
    # 平台通用资料：仅首批生成。追加批次重复输出会把版本再推进一次、
    # 造成无意义的知识版本变化，所以默认只在不含 offset 时输出。
    if include_platform:
        documents.extend({'id': pid, 'coach_id': None, 'title': title,
                          'source': f'demo://platform/{pid.removeprefix("platform-")}',
                          'document_type': 'platform', 'version': '2', 'content': content, 'is_demo': True}
                         for pid, title, content in PLATFORM_DOCS)
    return {'coaches': coaches, 'documents': documents}


def main():
    parser = argparse.ArgumentParser(description='生成指定数量的虚构演示教练数据')
    parser.add_argument('--count', type=int, default=50)
    parser.add_argument('--output', default='data/demo/catalog-50.json')
    parser.add_argument('--seed', type=int, default=20261015)
    parser.add_argument('--offset', type=int, default=0,
                        help='姓名池起始下标；追加批次时设为已有教练数')
    parser.add_argument('--id-start', type=int, default=1,
                        help='这批教练的起始编号，例如 51 生成 coach-51..')
    parser.add_argument('--price-shift', type=int, default=0,
                        help='价格网格错位（元）；与既有批次错开以避免撞价')
    parser.add_argument('--no-platform', action='store_true',
                        help='不输出平台通用文档（追加批次用）')
    args = parser.parse_args()
    dataset = generate(args.count, args.seed, offset=args.offset, id_start=args.id_start,
                       price_shift=args.price_shift, include_platform=not args.no_platform)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding='utf-8')
    ids = [c['id'] for c in dataset['coaches']]
    print(f'已写入 {target}：{len(dataset["coaches"])}位虚构教练（{ids[0]}..{ids[-1]}）、'
          f'{len(dataset["documents"])}篇文档')


if __name__ == '__main__':
    main()
