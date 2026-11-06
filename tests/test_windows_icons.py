"""Reuse existing ICO and exercise the real loader boundary with fake Win32 calls."""
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('windows_icon_test',ROOT/'packaging/windows-beta/windows_tray.py')
tray=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tray)


def test_existing_brand_ico_has_small_and_large_images():
    body=(ROOT/'packaging/windows-beta/remember-me.ico').read_bytes()
    reserved,kind,count=struct.unpack_from('<HHH',body)
    assert reserved==0 and kind==1 and count>=2
    sizes=set()
    for index in range(count):
        w,h,_,_,_,bits,length,offset=struct.unpack_from('<BBBBHHII',body,6+16*index)
        assert bits==32 and offset+length<=len(body)
        sizes.add(w or 256)
    assert {16,32,48} <= sizes


def test_loader_uses_unicode_file_and_pointer_sized_handle(monkeypatch,tmp_path):
    calls=[]
    def load(*args):
        calls.append(args)
        return 2**40
    monkeypatch.setattr(tray.ctypes,'WinDLL',lambda *a,**k: SimpleNamespace(LoadImageW=load),raising=False)
    root=tmp_path/'中文 program'
    assert tray.load_brand_icon(root)==2**40
    assert calls[0][1]==str(root/'remember-me.ico')
    assert calls[0][2:]==(1,0,0,0x50)
    assert load.restype==tray.W.HANDLE


def test_missing_icon_fails_visibly_not_silent_generic_icon(monkeypatch,tmp_path):
    def load(*args): return 0
    monkeypatch.setattr(tray.ctypes,'WinDLL',lambda *a,**k: SimpleNamespace(LoadImageW=load),raising=False)
    monkeypatch.setattr(tray.ctypes,'get_last_error',lambda:2,raising=False)
    monkeypatch.setattr(tray.ctypes,'WinError',lambda n:OSError(n,'missing icon'),raising=False)
    with pytest.raises(OSError,match='missing icon'):
        tray.load_brand_icon(tmp_path)


def test_full_menu_matches_existing_web_routes_and_chinese_labels():
    assert [fragment for _,_,fragment in tray.PAGES]==['','#memories','#stats','#ops','#help']
    assert len({command for command,_,_ in tray.PAGES})==len(tray.PAGES)
    assert tray.menu_text('zh-CN','Stats')=='统计'
    assert tray.menu_text('en','Stats')=='Stats'
    assert tray.menu_text('zh-CN','Run automatically at login')=='登录时自动启动'
    assert tray.menu_text('zh-CN','Exit')=='安全退出'


def test_windows_icon_uses_more_of_the_canvas_without_changing_web_asset():
    from PIL import Image
    source = Image.open(ROOT/'web/static/img/brand/logo-neutral-128.png').convert('RGBA')
    icon = Image.open(ROOT/'packaging/windows-beta/remember-me.ico').convert('RGBA')
    old = source.getbbox()
    new = icon.getbbox()
    assert (new[3]-new[1])/icon.height > 0.93
    assert (old[3]-old[1])/source.height < 0.8
