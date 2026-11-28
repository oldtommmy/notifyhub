"""Feishu layouts with optional callback buttons and numbered-reply fallback."""
import re
from datetime import datetime

HELP_TITLE = 'NotifyHub · 续聊菜单'
HELP_BACKGROUND = 'cus-0'


def escape(value):
    return re.sub(r'([\\`*_[\]<>])', r'\\\1', str(value or ''))


def markdown(text):
    return {'tag': 'div', 'text': {'tag': 'lark_md', 'content': text}}


def session_details(session):
    model = session.get('model') or '沿用会话配置'
    status = {
        'running': '正在运行', 'idle': '空闲', 'queued': '消息排队中',
        'waiting_input': '等待回复', 'waiting_approval': '等待桌面确认',
        'submitted': '提交状态待确认', 'unfinished': '已开始 · 尚无结束记录',
        'completed': '已完成', 'interrupted': '已中断', 'failed': '异常结束',
        'historical': '历史会话 · 可恢复', 'unavailable': '暂不可读取',
    }.get(session.get('status'), '状态待确认')
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
        ('状态 · /status', '查看当前会话和运行状态'),
        ('最新回答 · /latest', '查看当前会话最近的文字回答'),
        ('待回复 · /pending', '查看正在等待你处理的问题'),
        ('回复 原文 · /reply 原文', '把原文发给会话，例如「回复 1」'),
    )
    if view.get('text_questions'):
        commands += (('文字问答 任务 · /ask 任务', '已配置提问工具时，选项可直接回 A、B 或 C'),)
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
            {'tag': 'action', 'actions': [
                button('查看当前会话', action_value('status', '', 'current')),
                button('最新回答', action_value('latest_answer', '', 'current')),
                button('待回复', action_value('pending_input', '', 'current')),
            ]},
            {'tag': 'note', 'elements': [
                {'tag': 'plain_text', 'content': '在列表点「继续此会话」，在回答卡片可点「切回此会话」。'},
            ]},
        ])
    return elements


def build_full_answer_card(view, pages, page, answer_id, action_value):
    start, end = pages[page]
    engine = view.get('engine')
    title = f'{engine} · 完整回答' if engine else '完整回答'
    elements = [{'tag': 'markdown', 'content': view['text'][start:end]}]
    if len(pages) > 1:
        elements.append({'tag': 'note', 'elements': [
            {'tag': 'plain_text', 'content': f'第 {page + 1} 段，共 {len(pages)} 段'},
        ]})
    controls = []
    if page > 0:
        controls.append(button('上一段', action_value('view_answer', f'{answer_id}:{page - 1}')))
    if page + 1 < len(pages):
        controls.append(button('下一段', action_value('view_answer', f'{answer_id}:{page + 1}'), 'primary'))
    controls.append(button('收起回答', action_value('view_answer', f'{answer_id}:-1')))
    elements.append({'tag': 'action', 'actions': controls})
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'blue', 'title': {'tag': 'plain_text', 'content': title}},
            'elements': elements}


def build_native_card(view, max_chars=3000, action_value=None, *, logo_key=None, answer_id=None):
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
        labels = {'bound': '已切换会话', 'status': '当前会话', 'accepted': '消息已提交',
                  'latest_answer': '最新回答', 'pending_input': '待回复',
                  'result': '回答已完成' if view.get('ok') else '本轮未正常完成'}
        title = labels.get(kind, 'dsh 续聊')
        color = 'green' if kind == 'bound' or kind == 'result' and view.get('ok') else 'orange' if kind == 'result' else 'blue'
        elements.append(markdown(f"**{escape(session.get('title') or '未命名会话')}**\n{session_details(session)}"))
        elements.append({'tag': 'hr'})
        if kind in ('result', 'latest_answer'):
            text = str(view.get('text') or '暂未读取到文字回答。')
            if len(text) > max_chars:
                suffix = '内容截断，点「显示完整回答」查看全文' if answer_id and action_value else '内容截断，完整回答见桌面原会话'
                text = text[:max_chars] + f'\n\n（{suffix}）'
            elements.append(markdown(text))
            if kind == 'result':
                elements.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f"第 {view.get('turn', '?')} 轮 · 耗时 {view.get('duration', 0)} 秒 · 已写回原会话"}]})
            else:
                elements.append(markdown('要回复这条回答，可发送 **回复 你的答复**。'))
        elif kind == 'pending_input' and view.get('questions'):
            questions = view['questions']
            for index, question in enumerate(questions, 1):
                elements.append(markdown(f"**{index}. {escape(question['question'][:700])}**"))
                options = question.get('options', [])
                if options:
                    lines = []
                    for number, option in enumerate(options, 1):
                        line = f"{number}. {escape(option['label'][:180])}"
                        if option.get('description'):
                            line += f" · {escape(option['description'][:200])}"
                        lines.append(line)
                    elements.append(markdown('\n'.join(lines)))
                if (action_value and view.get('question_ref') and len(questions) == 1
                        and not question.get('multi_select')):
                    choices = [button(option['label'][:28], action_value('answer_input', {
                        'session': session.get('id'), 'ref': view['question_ref'], 'label': option['label'],
                    })) for option in options[:5]]
                    if choices:
                        elements.append({'tag': 'action', 'actions': choices})
        elif kind == 'accepted':
            elements.append(markdown('消息已进入原会话队列。若桌面正在执行，当前轮结束后继续处理。\n需要审批时，请在对应的桌面应用处理。'))
        elif kind == 'bound':
            elements.append(markdown('直接发消息继续聊。发 **会话** 切换对话，发 **状态** 查看当前会话。'))
        if view.get('detail'):
            elements.append(markdown(escape(view['detail'])))
        if view.get('activity_at'):
            as_of = datetime.fromtimestamp(view['activity_at']).astimezone().strftime('%m-%d %H:%M')
            elements.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f'记录时间 {as_of}'}]})
        if kind in ('bound', 'status'):
            elements.append({'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': f"目录：{session.get('cwd') or '未指定'}\n会话：{session.get('id') or '未知'}"}]})
    if action_value and kind != 'help':
        controls = [button('刷新列表', action_value('attach_list', '')),
                    button('刷新状态' if kind == 'status' else '当前会话', action_value('status', ''))]
        if kind == 'sessions':
            page = view.get('page', 0)
            if page > 0:
                controls.append(button('上一页', action_value('attach_list', str(page - 1))))
            if page + 1 < view.get('pages', 1):
                controls.append(button('下一页', action_value('attach_list', str(page + 1))))
        if kind not in ('sessions', 'pending_input') and session.get('id'):
            label = '切回此会话' if kind in ('result', 'latest_answer') else '继续此会话'
            controls.insert(0, button(label, action_value('attach', session['id']), 'primary'))
        if kind == 'pending_input':
            controls.append(button('刷新待回复', action_value(kind, '')))
        if kind in ('result', 'latest_answer') and answer_id:
            controls.insert(0, button('显示完整回答', action_value('view_answer', f'{answer_id}:0'), 'primary'))
        if kind in ('bound', 'status', 'pending_input'):
            controls.append(button('最新回答', action_value('latest_answer', '')))
        if kind in ('bound', 'status', 'latest_answer'):
            controls.append(button('待回复', action_value('pending_input', '')))
        controls.append(button('使用帮助', action_value('help', '')))
        elements.extend({'tag': 'action', 'actions': controls[start:start + 5]} for start in range(0, len(controls), 5))
    if view.get('engine') in ('Codex', 'Antigravity'):
        title = view['engine'] + ' · ' + title
    elif kind in ('status', 'latest_answer', 'pending_input'):
        title = 'DeepSeek Harness · ' + title
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
