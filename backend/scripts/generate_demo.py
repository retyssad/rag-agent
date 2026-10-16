"""明确标记的虚构演示数据；不代表真实教练资料。"""
import json
from pathlib import Path


def generate():
    names = ['林知夏', '陈远舟', '许安宁', '周亦辰', '苏沐晴', '陆景行', '沈初禾', '顾明川',
             '唐予安', '叶子清', '程北望', '夏星野', '温书言', '江若溪', '宋时雨', '贺南风']
    prices = [280, 300, 240, 380, 320, 260, 420, 200, 290, 350, 230, 450, 270, 310, 180, 360]
    districts = ['南山区', '南山区', '南山区', '福田区', '南山区', '宝安区', '南山区', '罗湖区',
                 '南山区', '福田区', '宝安区', '南山区', '福田区', '南山区', '罗湖区', '福田区']
    coaches, documents = [], []
    for i, name in enumerate(names):
        coach_id = f'coach-{i+1:02d}'
        goal = ['减脂', '增肌', '减脂', '力量', '减脂', '体能', '体态', '减脂'][i % 8]
        is_weekend = i in [2, 5, 8, 10, 14]
        availability = [{'days': [5, 6] if is_weekend else [0, 2, 4],
                         'start': 9 if is_weekend else 18, 'end': 17 if is_weekend else 22}]
        specialties = [goal, '新手入门' if i % 2 == 0 else '力量基础']
        coaches.append({'id': coach_id, 'name': name, 'price': prices[i], 'districts': [districts[i]],
            'delivery_modes': ['线下', '线上'] if i % 3 == 0 else ['线下'], 'specialties': specialties,
            'qualifications': [{'title': '演示训练指导证书（虚构）', 'verified': False, 'scope': '非医疗资质'}],
            'courses': [{'name': f'{goal}基础私教课', 'duration_minutes': 60, 'price': prices[i]}],
            'availability': availability, 'bio': f'{name}是一位用于演示选择流程的虚构教练。', 'is_demo': True})
        if i == 15:
            continue  # 明确覆盖资料不足。
        patient = i % 2 == 0
        teaching = (f'{name}面向{goal}目标提供循序渐进的基础训练。' +
            ('对新手耐心，习惯逐步解释动作，先讲解再示范，并允许学员重复提问。' if patient else
             '偏好用训练记录跟踪负重与次数，强调每周复盘。对沟通耐心程度，现有资料没有说明。'))
        case = (f'一份虚构的{goal}教学案例记录：学员先学习基础动作，再根据训练反馈逐步调整组数与强度。'
                '案例只用于演示资料引用，不代表真实效果，也不能推算成功率。')
        if i in [0, 4, 6, 12]:
            case += (f'在另一个虚构案例中，{name}陪同一位自述膝盖不适且已获得医生运动许可的学员，'
                     '使用低冲击活动、控制动作幅度，并记录不适反馈。这是训练调整记录，不是医疗诊断或康复资质。')
        documents.append({'id': f'{coach_id}-teaching', 'coach_id': coach_id, 'title': f'{name}教学资料（虚构）',
            'source': f'demo://coaches/{coach_id}/teaching', 'document_type': 'teaching', 'version': '1',
            'content': '# 教学方式\n\n' + teaching, 'is_demo': True})
        documents.append({'id': f'{coach_id}-case', 'coach_id': coach_id, 'title': f'{name}训练案例（虚构）',
            'source': f'demo://coaches/{coach_id}/case', 'document_type': 'case', 'version': '1',
            'content': '# 训练案例\n\n' + case, 'is_demo': True})
    general = [
        ('fat-loss-course', '平台减脂基础课程（演示）', '# 减脂课程安排\n\n平台演示减脂课程按四周安排：第一周了解基础动作与训练记录；第二周加入全身力量训练；第三周根据反馈增加适量有氧；第四周复盘出勤与动作表现。每周建议两次私教训练，其他活动量由教练与学员共同确认。\n\n# 个体调整\n\n课程安排是演示模板，训练频次和强度需根据个人基础与恢复情况调整，不承诺减重幅度。'),
        ('overload', '渐进超负荷学习笔记（演示）', '# 渐进超负荷\n\n渐进超负荷是随身体适应逐渐增加训练刺激。可通过负重、重复次数、组数或动作控制调整。一次优先改变一个变量，避免每次都同时增加负重和训练量。\n\n# 恢复与记录\n\n先记录完成质量和疲劳感，再决定下一次变化。训练进步不是每次都必须加重量。'),
        ('beginner', '新手训练说明（演示）', '# 新手入门\n\n新手可先从每周两次全身力量训练开始，动作包括推、拉、下肢与躯干稳定。先掌握动作质量再增加负荷，训练之间留出恢复时间。'),
        ('safety', '运动不适处理原则（演示）', '# 运动不适\n\n训练中出现明显疼痛时应停止引发疼痛的动作。疼痛持续或反复时先寻求医疗评估，不根据网络问答作诊断。教练训练经验与医疗诊断、康复治疗资质是不同事实。'),
    ]
    documents.extend({'id': f'platform-{id}', 'coach_id': None, 'title': title,
        'source': f'demo://platform/{id}', 'document_type': 'platform', 'version': '1',
        'content': content, 'is_demo': True} for id, title, content in general)
    return {'coaches': coaches, 'documents': documents}


if __name__ == '__main__':
    target = Path('data/demo/dataset.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(generate(), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'已写入 {target}（16位虚构教练、34篇演示文档）')
