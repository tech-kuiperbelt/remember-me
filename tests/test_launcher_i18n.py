"""Launcher language and state presentation without acquiring GUI resources."""
import pytest

from launcher.main import LauncherUI, _language, _translate


@pytest.mark.parametrize(('environment', 'expected'), [
    ({}, 'en'),
    ({'LANG': 'zh_CN.UTF-8'}, 'zh-CN'),
    ({'LANG': 'zh-SG.UTF-8'}, 'zh-CN'),
    ({'LANG': 'zh-Hans-CN'}, 'zh-CN'),
    ({'LANG': 'zh_TW.UTF-8'}, 'en'),
    ({'LANG': 'fr_FR.UTF-8'}, 'en'),
    ({'LANG': 'zh_CN.UTF-8', 'LC_MESSAGES': 'en_US.UTF-8'}, 'en'),
    ({'LANG': 'en_US.UTF-8', 'LC_MESSAGES': 'zh_CN.UTF-8', 'LC_ALL': 'C'}, 'en'),
])
def test_desktop_language_selection(environment, expected):
    assert _language(environment) == expected


def test_chinese_menu_and_window_labels_are_translated():
    translations = {
        'Open Remember Me': '打开 Remember Me', 'Memories': '记忆', 'Stats': '统计',
        'Settings': '设置', 'Help': '帮助', 'Run automatically at login': '登录时自动启动',
        'Quit Remember Me': '退出 Remember Me', 'System tray unavailable': '系统托盘不可用',
    }
    for english, chinese in translations.items():
        assert _translate('zh-CN', english) == chinese
        assert _translate('en', english) == english


@pytest.mark.parametrize(('state', 'zh_label', 'can_open'), [
    ('Running', '● 运行中', True), ('Starting', '◌ 启动中', False),
    ('Stopping', '◌ 正在停止', False), ('Stopped', '○ 已停止', False),
    ('Error', '! 错误', False), ('Maintenance', '● 维护中', True),
])
def test_state_and_explicit_open_action(state, zh_label, can_open):
    class Autostart:
        def enabled(self):
            return False
    ui = LauncherUI(None, Autostart(), (None, None, None, None, None))
    ui.language = 'zh-CN'
    ui.snapshot = {'state': state, 'url': 'http://127.0.0.1:1314', 'error': None}
    assert ui._status_text() == zh_label
    assert ui._can_open() is can_open
    assert ui._status_action_text() == (zh_label + ' — 打开 Remember Me' if can_open else zh_label)
    ui.language = 'en'
    assert state in ui._status_text()
    assert ('Open Remember Me' in ui._status_action_text()) is can_open


def test_error_detail_is_kept_in_window_but_not_repeated_in_menu():
    class Autostart:
        def enabled(self):
            return False
    ui = LauncherUI(None, Autostart(), (None, None, None, None, None))
    ui.language = 'zh-CN'
    ui.snapshot = {'state': 'Error', 'url': '', 'error': 'technical detail'}
    assert ui._status_text() == '! 错误: technical detail'
    assert ui._status_action_text() == '! 错误'
