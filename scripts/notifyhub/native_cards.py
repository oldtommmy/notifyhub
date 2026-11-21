"""Feishu layouts for native continuation; no callback subscription required."""
import re


def escape(value):
    return re.sub(r'([\\`*_[\]<>])', r'\\\1', str(value or ''))


def markdown(text):
    return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': text}}


def session_details(session):
    model = session.get('model') or '沿用会话配置'
    status = {'running': '正在运行', 'idle': '空闲', 'completed': '已完成', 'failed': '异常结束'}.get(session.get('status'), '状态未知')
    return f"📁 {escape(session.get('project'))}　·　{status}\n模型：{escape(model)}"


def build_native_card(view, max_chars=3000):
    kind = view.get('type')
    session = view.get('session') or {}
    elements = []
    color = 'blue'
    if kind == 'sessions':
        title = '选择要继续的会话'
        rows = view.get('sessions') or []
        elements.append(markdown('**回复序号即可切换**，例如回复 `2`。选择在 5 分钟内有效。'))
        for index, row in enumerate(rows):
            elements.append({'tag': 'hr'})
            current = ' · 当前会话' if row.get('current') else ''
            elements.append(markdown(f"**{index + 1} · {escape(row.get('title'))}**{current}\n{session_details(row)}"))
        if not rows:
            elements.append(markdown('暂无已加载的会话。请先在桌面 dsh 打开一个对话，再发「会话」。'))
        elements.append(markdown('只显示当前宿主已加载的前 10 个主会话。'))
    else:
        labels = {'bound': '已切换会话', 'status': '当前会话', 'accepted': '消息已提交', 'result': '回答已完成' if view.get('ok') else '本轮未正常完成'}
        title = labels.get(kind, 'dsh 续聊')
        color = 'green' if kind == 'bound' or kind == 'result' and view.get('ok') else 'orange' if kind == 'result' else 'blue'
        elements.append(markdown(f"**{escape(session.get('title') or '未命名会话')}**\n{session_details(session)}"))
        elements.append({'tag': 'hr'})
        if kind == 'result':
            text = str(view.get('text') or '本轮没有文本回答。')
            if len(text) > max_chars:
                text = text[:max_chars] + '\n\n（内容截断，完整回答见桌面原会话）'
            elements.append(markdown(text))
            elements.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f"第 {view.get('turn', '?')} 轮 · 耗时 {view.get('duration', 0)} 秒 · 已写回原会话"}]})
        elif kind == 'accepted':
            elements.append(markdown('消息已进入原会话队列。若桌面正在执行，当前轮结束后继续处理。\n需要审批时，请在桌面 dsh 处理。'))
        else:
            elements.append(markdown('直接发消息继续聊。发 **会话** 切换对话，发 **状态** 查看当前会话。'))
        if kind in ('bound', 'status'):
            elements.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f"目录：{session.get('cwd') or '未指定'}\n会话：{session.get('id') or '未知'}"}]})
    return {'config': {'wide_screen_mode': True}, 'header': {'template': color, 'title': {'tag': 'plain_text', 'content': title}}, 'elements': elements}
