"""autohost 纯逻辑单元测试（不依赖网络 / pytest）。

运行：python tests/test_autohost.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autohost import (load_playlist, order_playlist, read_paragraphs,
                      PlaylistIterator, parse_args, ConnectionLost,
                      wait_until_spoken, wait_for_idle)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   - {name}")
    else:
        _failed += 1
        print(f"  FAIL - {name}")


def make_content(tmp, stories=(), songs=()):
    os.makedirs(os.path.join(tmp, 'stories'), exist_ok=True)
    os.makedirs(os.path.join(tmp, 'songs'), exist_ok=True)
    for name, text in stories:
        with open(os.path.join(tmp, 'stories', name), 'w', encoding='utf-8') as f:
            f.write(text)
    for name in songs:
        with open(os.path.join(tmp, 'songs', name), 'wb') as f:
            f.write(b'\x00')
    return tmp


def test_load_playlist():
    with tempfile.TemporaryDirectory() as tmp:
        make_content(tmp,
                     stories=[('02.txt', 'b'), ('01.txt', 'a')],
                     songs=['s2.wav', 's1.mp3'])
        items = load_playlist(tmp)
        types = [i['type'] for i in items]
        names = [i['name'] for i in items]
        check("故事在前歌曲在后", types == ['story', 'story', 'song', 'song'])
        check("故事按文件名排序", names[:2] == ['01.txt', '02.txt'])
        check("歌曲按文件名排序(mp3+wav)", names[2:] == ['s1.mp3', 's2.wav'])


def test_load_empty():
    with tempfile.TemporaryDirectory() as tmp:
        make_content(tmp)
        check("空目录返回空清单", load_playlist(tmp) == [])


def test_order_alternate():
    items = [
        {'type': 'story', 'name': 'a'}, {'type': 'story', 'name': 'b'},
        {'type': 'story', 'name': 'c'}, {'type': 'song', 'name': 'x'},
    ]
    out = [i['name'] for i in order_playlist(items, 'alternate')]
    # 交替：故事,歌曲,故事,故事(歌曲用完继续放剩余故事)
    check("alternate 交替且不丢条目", out == ['a', 'x', 'b', 'c'])
    check("alternate 条目数不变", len(out) == len(items))


def test_order_random_keeps_all():
    items = [{'type': 'story', 'name': str(i)} for i in range(6)]
    out = order_playlist(items, 'random')
    check("random 不丢不重", sorted(i['name'] for i in out) == [str(i) for i in range(6)])


def test_iterator_loop():
    items = [{'type': 'story', 'name': 'a'}, {'type': 'song', 'name': 'b'}]
    it = PlaylistIterator(items, order='sequential', loop=True)
    seq = [it.next()['name'] for _ in range(5)]
    check("loop=true 循环重复", seq == ['a', 'b', 'a', 'b', 'a'])
    check("loop=true 永远 has_next", it.has_next())


def test_iterator_no_loop():
    items = [{'type': 'story', 'name': 'a'}, {'type': 'song', 'name': 'b'}]
    it = PlaylistIterator(items, order='sequential', loop=False)
    check("first", it.next()['name'] == 'a')
    check("second", it.next()['name'] == 'b')
    check("no_loop 播完 has_next=False", not it.has_next())
    check("no_loop 播完 next=None", it.next() is None)


def test_iterator_empty_raises():
    try:
        PlaylistIterator([], loop=True)
        check("空清单应抛异常", False)
    except ValueError:
        check("空清单抛 ValueError", True)


def test_read_paragraphs():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, 's.txt')
        with open(p, 'w', encoding='utf-8') as f:
            f.write("标题\n\n第一段。\n\n\n第二段。\n\n")
        paras = read_paragraphs(p)
        check("按空行分段并去空段", paras == ['标题', '第一段。', '第二段。'])


def test_read_paragraphs_bom():
    """记事本存的 .txt 带 UTF-8 BOM，不能把 BOM 当正文念出来。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, 's.txt')
        with open(p, 'wb') as f:
            f.write(b'\xef\xbb\xbf' + "第一段。\n\n第二段。".encode('utf-8'))
        paras = read_paragraphs(p)
        check("带 BOM 的文件首段不含 BOM", paras == ['第一段。', '第二段。'])


def test_parse_args_loop():
    check("loop 默认 true", parse_args([]).loop is True)
    check("loop=false 解析为 False", parse_args(['--loop', 'false']).loop is False)
    check("gap 解析", parse_args(['--gap', '7']).gap == 7.0)


# ─── 连接中断：等待循环必须有出口 ────────────────────────────────────────────

class _DeadClient:
    """模拟服务挂掉：所有调用都失败。复用真实的失败计数逻辑。"""

    def __init__(self, max_fails=3):
        self.max_fails = max_fails
        self._fails = 0
        self.calls = 0

    def is_speaking(self, sessionid):
        self.calls += 1
        self._fails += 1
        if self._fails >= self.max_fails:
            raise ConnectionLost(f"连续 {self._fails} 次失败")
        return True  # 保守：出错时假定在说话，别打断


def test_wait_until_spoken_gives_up_on_dead_server():
    c = _DeadClient(max_fails=3)
    try:
        wait_until_spoken(c, '0', start_timeout=5, poll=0.01)
        check("服务挂掉时 wait_until_spoken 应抛 ConnectionLost", False)
    except ConnectionLost:
        check("服务挂掉时 wait_until_spoken 抛 ConnectionLost 而非死循环", True)


def test_wait_for_idle_gives_up_on_dead_server():
    c = _DeadClient(max_fails=3)
    try:
        wait_for_idle(c, '0', gap=1, poll=0.01)
        check("服务挂掉时 wait_for_idle 应抛 ConnectionLost", False)
    except ConnectionLost:
        check("服务挂掉时 wait_for_idle 抛 ConnectionLost 而非死循环", True)


def test_client_fail_streak_resets_on_success():
    from autohost import LiveTalkingClient
    c = LiveTalkingClient('http://x', max_fails=3)
    c._note_fail()
    c._note_fail()
    check("失败 2 次未达阈值", c._fails == 2)
    c._note_ok()
    check("一次成功清零失败计数", c._fails == 0)
    c._note_fail(); c._note_fail()
    try:
        c._note_fail()
        check("达到阈值应抛 ConnectionLost", False)
    except ConnectionLost:
        check("连续失败达阈值抛 ConnectionLost", True)


if __name__ == '__main__':
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, '__name__', '').startswith('test_'):
            print(f"\n{fn.__name__}:")
            fn()
    print(f"\n==== {_passed} passed, {_failed} failed ====")
    sys.exit(1 if _failed else 0)
