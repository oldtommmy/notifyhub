"""Feishu layouts with optional callback buttons and numbered-reply fallback."""
import re

HELP_TITLE = 'NotifyHub · 续聊菜单'
HELP_BACKGROUND = 'cus-0'


def escape(value):
    return re.sub(r'([\\`*_[\]<>])', r'\\\1', str(value or ''))


def markdown(text):
    return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': text}}


def session_details(session):
    model = session.get('model') or '沿用会话配置'
    status = {'running': '正在运行', 'idle': '空闲', 'completed': '已完成', 'failed': '异常结束', 'historical': '历史会话 · 选择后恢复', 'unavailable': '暂不可读取'}.get(session.get('status'), '状态未知')
    if session.get('loaded'):
        status = '已加载 · ' + status
    return f"📁 {escape(session.get('project'))}　·　{status}\n模型：{escape(model)}"


def help_banner(logo_key, description):
    return {
        'tag': 'column_set',
        'flex_mode': 'none',
        'horizontal_spacing': 'default',
        'columns': [
            {'tag': 'column', 'width': 'weighted', 'weight': 3, 'vertical_align': 'center',
             'background_style': HELP_BACKGROUND,
             'elements': [markdown(f'**{HELP_TITLE}**\n{description}')]},
            {'tag': 'column', 'width': 'weighted', 'weight': 1, 'vertical_align': 'center',
             'elements': [{'tag': 'img', 'img_key': logo_key, 'mode': 'fit_horizontal',
                           'alt': {'tag': 'plain_text', 'content': 'NotifyHub Logo'}}]},
        ],
    }


def help_elements(view, action_value, logo_key=None):
    engines = [(engine, name) for engine, name in (
        ('codex', 'Codex'), ('dsh', 'DSH'), ('antigravity', 'Antigravity'),
    ) if view.get(engine)]
    description = ('先选一个桌面会话，再直接发消息。回答会回到飞书。' if engines else
                   '此聊天暂无可用的续聊入口。请与机器人私聊，并在 NotifyHub 配置中启用续聊。')
    elements = [help_banner(logo_key, description) if logo_key else markdown(description)]
    if not engines:
        return elements
    if action_value:
        controls = [button(f'{label} 会话', action_value('attach_list', '', engine), 'primary')
                    for engine, label in engines]
        elements.append({'tag': 'action', 'actions': controls})
    engine_commands = ' · '.join(f'{label}会话' for _, label in engines)
    commands = (
        ('会话 · /sessions', '列出当前工具的会话'),
        ('2 · /attach 2', '选中本页第 2 个会话'),
        ('上一页 · 下一页', '翻动会话列表'),
        ('状态 · /status', '查看当前绑定'),
    )
    elements.extend([
        markdown(f'**切换工具**\n{engine_commands}'),
        {'tag': 'hr'},
        {'tag': 'div', 'fields': [
            {'is_short': True, 'text': {'tag': 'lark_md', 'content': f'**{command}**\n{description}'}}
            for command, description in commands
        ]},
        markdown('**打开菜单**\n帮助 · 命令 · 菜单 · help · command'),
    ])
    if action_value:
        elements.extend([
            {'tag': 'action', 'actions': [button('查看当前会话', action_value('status', '', 'current'))]},
            {'tag': 'note', 'elements': [
                {'tag': 'plain_text', 'content': '在列表点「继续此会话」，在回答卡片可点「切回此会话」。'},
            ]},
        ])
    return elements


def build_native_card(view, max_chars=3000, action_value=None, *, logo_key=None):
    kind = view.get('type')
    session = view.get('session') or {}
    elements = []
    color = 'blue'
    if kind == 'help':
        title = HELP_TITLE
        elements = help_elements(view, action_value, logo_key)
    elif kind == 'sessions':
        title = '选择要继续的会话'
        rows = view.get('sessions') or []
        elements.append(markdown('**回复序号即可切换**，例如回复「2」。选择在 5 分钟内有效。'))
        for index, row in enumerate(rows):
            elements.append({'tag': 'hr'})
            current = ' · 当前会话' if row.get('current') else ''
            elements.append(markdown(f"**{index + 1} · {escape(row.get('title'))}**{current}\n{session_details(row)}"))
            if action_value:
                elements.append({'tag': 'action', 'actions': [button('继续此会话', action_value('attach', row['id']), 'primary')]})
        if not rows:
            hint = ('先在 Antigravity 桌面完成一轮对话，再发「Antigravity会话」。'
                    if view.get('engine') == 'Antigravity' else '请先在桌面打开一个对话，再发「会话」。')
            elements.append(markdown('暂无可读取的会话。' + hint))
        elements.append(markdown((f"共 {view['total']} 个主会话 · " if view.get('total') is not None else '') + f"第 {view.get('page', 0) + 1} 页 · 按最近活动排序"))
        if view.get('pages', 1) > 1:
            elements.append(markdown('发送 **上一页** 或 **下一页** 翻页。'))
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
            elements.append(markdown('消息已进入原会话队列。若桌面正在执行，当前轮结束后继续处理。\n需要审批时，请在对应的桌面应用处理。'))
        else:
            elements.append(markdown('直接发消息继续聊。发 **会话** 切换对话，发 **状态** 查看当前会话。'))
        if kind in ('bound', 'status'):
            elements.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f"目录：{session.get('cwd') or '未指定'}\n会话：{session.get('id') or '未知'}"}]})
    if action_value and kind != 'help':
        controls = [button('刷新列表', action_value('attach_list', '')), button('当前会话', action_value('status', ''))]
        if kind == 'sessions':
            page = view.get('page', 0)
            if page > 0:
                controls.append(button('上一页', action_value('attach_list', str(page - 1))))
            if page + 1 < view.get('pages', 1):
                controls.append(button('下一页', action_value('attach_list', str(page + 1))))
        if kind != 'sessions' and session.get('id'):
            controls.insert(0, button('切回此会话' if kind == 'result' else '继续此会话', action_value('attach', session['id']), 'primary'))
        controls.append(button('使用帮助', action_value('help', '')))
        elements.append({'tag': 'action', 'actions': controls})
    if view.get('engine') in ('Codex', 'Antigravity'):
        title = view['engine'] + ' · ' + title
    card = {'config': {'wide_screen_mode': True}, 'elements': elements}
    if kind == 'help' and logo_key:
        card['config']['style'] = {'color': {HELP_BACKGROUND: {
            'light_mode': 'rgba(224,233,255,0.5)',
            'dark_mode': 'rgba(23,49,102,0.5)',
        }}}
    else:
        card['header'] = {'template': color, 'title': {'tag': 'plain_text', 'content': title}}
    return card


def button(label, value, style='default'):
    return {'tag': 'button', 'text': {'tag': 'plain_text', 'content': label},
            'type': style, 'value': value}
