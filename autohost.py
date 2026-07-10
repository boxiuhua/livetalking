###############################################################################
#  autohost.py — LiveTalking 数字人值守脚本
#
#  无人时自动轮流“讲故事 / 唱歌”，循环不息。纯外部驱动，不修改引擎。
#
#  用法：
#    1. 浏览器打开 http://127.0.0.1:8010/index.html 点“开始连接”
#    2. python autohost.py
#
#  故事放在 content/stories/*.txt（空行分段），歌曲放在 content/songs/*.wav|mp3
###############################################################################

import argparse
import glob
import json
import os
import random
import sys
import time

try:
    import requests
except ImportError:
    print("需要 requests 库：pip install requests")
    sys.exit(1)


class ConnectionLost(Exception):
    """与引擎连续多次通信失败。等待循环靠它退出，主循环靠它触发重连。"""


# 值守正在播什么，写在这里给 llm.py 读。故事文本走 echo 不经过大模型，
# 大模型不知道她嘴里在讲什么，被观众问到就会编。这个文件是它俩之间唯一的桥。
NOW_PLAYING = os.path.join('runtime', 'now_playing.json')


def write_now_playing(item):
    """记录当前节目；item 为 None 表示什么也没在播。

    故事连全文一起写：只给标题的话，大模型照样会编剧情。
    """
    try:
        os.makedirs(os.path.dirname(NOW_PLAYING), exist_ok=True)
        if item is None:
            payload = {'kind': 'idle'}
        else:
            title = os.path.splitext(item['name'])[0]
            title = title.split('-', 1)[-1]   # 去掉 "01-" 这样的排序前缀
            payload = {'kind': item['type'], 'title': title}
            if item['type'] == 'story':
                paras = read_paragraphs(item['path'])
                payload['text'] = '\n'.join(paras[1:])   # 首段是标题，去掉
        with open(NOW_PLAYING, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        print(f"[warn] 写 now_playing 失败（不影响播放）: {e}")


# ─── 内容加载 ────────────────────────────────────────────────────────────────

def load_playlist(content_dir):
    """扫描内容目录，返回条目列表。

    条目：{'type': 'story'|'song', 'path': 绝对路径, 'name': 文件名}
    故事按文件名排序，歌曲按文件名排序，故事在前。
    """
    stories_dir = os.path.join(content_dir, 'stories')
    songs_dir = os.path.join(content_dir, 'songs')
    items = []
    for p in sorted(glob.glob(os.path.join(stories_dir, '*.txt'))):
        items.append({'type': 'story', 'path': p, 'name': os.path.basename(p)})
    song_paths = (glob.glob(os.path.join(songs_dir, '*.wav'))
                  + glob.glob(os.path.join(songs_dir, '*.mp3')))
    for p in sorted(song_paths):
        items.append({'type': 'song', 'path': p, 'name': os.path.basename(p)})
    return items


def order_playlist(items, order):
    """按策略排序：sequential / random / alternate（故事歌曲交替）。"""
    if order == 'random':
        out = items[:]
        random.shuffle(out)
        return out
    if order == 'alternate':
        stories = [i for i in items if i['type'] == 'story']
        songs = [i for i in items if i['type'] == 'song']
        out = []
        for s, g in _zip_longest(stories, songs):
            if s is not None:
                out.append(s)
            if g is not None:
                out.append(g)
        return out
    return items[:]  # sequential


def _zip_longest(a, b):
    n = max(len(a), len(b))
    for i in range(n):
        yield (a[i] if i < len(a) else None,
               b[i] if i < len(b) else None)


def read_paragraphs(story_path):
    """把故事文本按空行切成段落，去掉空白段。

    用 utf-8-sig 打开：记事本存的 .txt 带 BOM，否则 BOM 会混进首段被念出来。
    """
    with open(story_path, 'r', encoding='utf-8-sig') as f:
        text = f.read()
    paras = [p.strip() for p in text.split('\n\n')]
    return [p for p in paras if p]


# ─── 播放清单迭代器 ──────────────────────────────────────────────────────────

class PlaylistIterator:
    """按顺序给出下一条；播完按 loop 决定是否重头（random 每轮重洗）。"""

    def __init__(self, items, order='sequential', loop=True):
        if not items:
            raise ValueError("播放清单为空")
        self._order = order
        self._loop = loop
        self._base = items
        self._queue = order_playlist(items, order)
        self._idx = 0

    def has_next(self):
        return self._idx < len(self._queue) or self._loop

    def next(self):
        if self._idx >= len(self._queue):
            if not self._loop:
                return None
            self._idx = 0
            if self._order == 'random':
                self._queue = order_playlist(self._base, 'random')
        item = self._queue[self._idx]
        self._idx += 1
        return item


# ─── LiveTalking HTTP 客户端 ─────────────────────────────────────────────────

class LiveTalkingClient:
    """封装引擎已有的 HTTP API。

    单次网络错误就地吞掉（服务偶尔抖一下不该中断值守）；连续 max_fails 次
    失败视为服务已挂，抛 ConnectionLost，交给主循环退避重连。
    """

    def __init__(self, server, timeout=8, max_fails=8):
        self.server = server.rstrip('/')
        self.timeout = timeout
        self.max_fails = max_fails
        self._fails = 0
        # trust_env=False：忽略系统代理变量，避免本地回环被代理拦截（返回空响应）
        self._s = requests.Session()
        self._s.trust_env = False
        self._s.proxies = {'http': None, 'https': None}

    def _note_ok(self):
        self._fails = 0

    def _note_fail(self):
        self._fails += 1
        if self._fails >= self.max_fails:
            raise ConnectionLost(f"连续 {self._fails} 次调用失败")

    def reset(self):
        """重连前清零失败计数。"""
        self._fails = 0

    def discover_session(self):
        """返回第一个活跃会话 id；无会话或出错返回 None（不抛，主循环会一直等）。"""
        try:
            r = self._s.get(self.server + '/api/admin/sessions', timeout=self.timeout)
            sessions = r.json().get('data', {}).get('sessions', [])
            self._note_ok()
            return sessions[0]['sessionid'] if sessions else None
        except Exception as e:
            print(f"[warn] 获取会话失败: {e}")
            return None

    def is_speaking(self, sessionid):
        """查询是否在说话；单次出错保守返回 True（避免打断），连续失败则抛。"""
        try:
            r = self._s.post(self.server + '/is_speaking',
                             json={'sessionid': sessionid}, timeout=self.timeout)
            data = bool(r.json().get('data', False))
            self._note_ok()
            return data
        except Exception as e:
            print(f"[warn] is_speaking 失败: {e}")
            self._note_fail()
            return True

    def say(self, sessionid, text, mode='echo', interrupt=False):
        """让数字人说话。

        mode='echo' 直接念 text；mode='chat' 把 text 交给大模型，念它的回答。
        interrupt=True 会先掐掉当前正在说的内容（插话用）。
        """
        payload = {'sessionid': sessionid, 'type': mode, 'text': text}
        if interrupt:
            payload['interrupt'] = True
        try:
            r = self._s.post(self.server + '/human', json=payload, timeout=self.timeout)
            ok = r.json().get('code') == 0
            self._note_ok()
            return ok
        except Exception as e:
            print(f"[warn] say 失败: {e}")
            self._note_fail()
            return False

    def play_audio(self, sessionid, audio_path):
        """播放一段音频文件（唱歌）。返回是否成功。"""
        with open(audio_path, 'rb') as f:   # 文件读不出来是内容问题，交给上层跳过
            try:
                r = self._s.post(self.server + '/humanaudio',
                                 data={'sessionid': sessionid},
                                 files={'file': (os.path.basename(audio_path), f)},
                                 timeout=max(self.timeout, 30))
                ok = r.json().get('code') == 0
                self._note_ok()
                return ok
            except Exception as e:
                print(f"[warn] play_audio 失败: {e}")
                self._note_fail()
                return False


# ─── 说话状态等待 ────────────────────────────────────────────────────────────

def wait_until_spoken(client, sessionid, start_timeout=10, idle_confirm=1.5, poll=0.4):
    """发出内容后，等它开始说话再等它说完（连续静默 idle_confirm 秒视为说完）。

    若 start_timeout 内一直没开口（如空文本），也返回，避免卡死。
    """
    t0 = time.time()
    started = False
    while time.time() - t0 < start_timeout:
        if client.is_speaking(sessionid):
            started = True
            break
        time.sleep(poll)
    if not started:
        return
    last_speaking = time.time()
    while True:
        if client.is_speaking(sessionid):
            last_speaking = time.time()
        elif time.time() - last_speaking >= idle_confirm:
            return
        time.sleep(poll)


def wait_for_idle(client, sessionid, gap, poll=0.5):
    """等到数字人已连续静默 gap 秒（有人在对话时会一直等）。"""
    last_speaking = time.time()
    while True:
        if client.is_speaking(sessionid):
            last_speaking = time.time()
        elif time.time() - last_speaking >= gap:
            return
        time.sleep(poll)


# ─── 播放单条内容 ────────────────────────────────────────────────────────────

def play_item(client, sessionid, item):
    write_now_playing(item)
    if item['type'] == 'story':
        paras = read_paragraphs(item['path'])
        print(f"[讲故事] {item['name']}（{len(paras)} 段）")
        for i, para in enumerate(paras, 1):
            print(f"   段落 {i}/{len(paras)}")
            if client.say(sessionid, para):
                wait_until_spoken(client, sessionid)
                time.sleep(0.6)  # 段落间小停顿
    elif item['type'] == 'song':
        print(f"[唱歌] {item['name']}")
        if client.play_audio(sessionid, item['path']):
            wait_until_spoken(client, sessionid, start_timeout=15)
    write_now_playing(None)


# ─── 主循环 ──────────────────────────────────────────────────────────────────

def run(args):
    # 管道 / 重定向下也实时刷新日志
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    items = load_playlist(args.content)
    if not items:
        print(f"[错误] {args.content} 里没有任何内容。请在 content/stories 放 .txt 故事，"
              f"或在 content/songs 放 .wav/.mp3 歌曲。")
        return 1
    n_story = sum(1 for i in items if i['type'] == 'story')
    n_song = sum(1 for i in items if i['type'] == 'song')
    print(f"内容清单：故事 {n_story} 篇，歌曲 {n_song} 首，顺序={args.order}，循环={args.loop}")

    client = LiveTalkingClient(args.server)
    playlist = PlaylistIterator(items, order=args.order, loop=args.loop)

    sessionid = None   # 即便指定了 --sessionid，也先确认服务可达再用
    print("值守启动。等待数字人会话…（请确保浏览器已打开 index.html 并点“开始连接”）")

    while playlist.has_next():
        try:
            # 1. 确保服务可达且有会话（--sessionid 也要过这道闸，重连时才不会空轮询）
            if not sessionid:
                found = client.discover_session()
                if not found:
                    print("   未发现活跃会话，5 秒后重试…", end='\r')
                    time.sleep(5)
                    continue
                sessionid = args.sessionid or found
                print(f"\n发现会话 {sessionid}，开始值守。")

            # 2. 等它空闲够 gap 秒（有人对话时自动等待）
            wait_for_idle(client, sessionid, args.gap)

            # 3. 播下一条
            item = playlist.next()
            if item is None:
                break
            try:
                play_item(client, sessionid, item)
            except (ConnectionLost, KeyboardInterrupt):
                raise
            except (OSError, ValueError) as e:
                print(f"[warn] 读不了内容，跳过 {item['name']}：{e}")
        except ConnectionLost as e:
            # 服务挂了或重启中：清空会话，退避后重新发现
            print(f"\n[warn] 与服务的连接中断（{e}），5 秒后重连…")
            client.reset()
            sessionid = args.sessionid or None
            time.sleep(5)
            continue

        time.sleep(args.gap * 0.5)

    print("播放清单结束（--loop=false）。值守退出。")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="LiveTalking 数字人值守：无人时自动讲故事/唱歌")
    p.add_argument('--server', default='http://127.0.0.1:8010', help='LiveTalking 服务地址')
    p.add_argument('--sessionid', default='', help='指定会话 id；留空则自动发现第一个活跃会话')
    p.add_argument('--gap', type=float, default=4.0, help='静默多少秒后播下一条')
    p.add_argument('--order', default='sequential',
                   choices=['sequential', 'random', 'alternate'], help='播放顺序')
    p.add_argument('--loop', default='true', help='播完是否重头循环 (true/false)')
    p.add_argument('--content', default='./content', help='内容根目录')
    args = p.parse_args(argv)
    args.loop = str(args.loop).lower() not in ('false', '0', 'no')
    return args


if __name__ == '__main__':
    try:
        sys.exit(run(parse_args()))
    except KeyboardInterrupt:
        print("\n已停止值守。")
