###############################################################################
#  talk.py — 数字人互动台
#
#  在值守讲故事的同时，你随时打字插话：数字人会立刻停下故事，说你要她说的话，
#  说完 autohost 自动接着讲下一段。不用停任何进程。
#
#  两种模式：
#    echo（默认）  她一字不差念出你打的字。不需要 API key。
#    chat          你打的字交给大模型，她念大模型的回答。需要 DASHSCOPE_API_KEY。
#
#  用法：
#    python talk.py                 # echo 模式
#    python talk.py --mode chat     # 大模型问答模式
#
#  运行中输入 /chat 或 /echo 可随时切换模式，/quit 退出。
###############################################################################

import argparse
import os
import sys

from autohost import ConnectionLost, LiveTalkingClient

BANNER = """\
─────────────────────────────────────────────
 数字人互动台   模式：{mode}
 直接打字回车 → 她立刻停下故事说这句话
 /echo 切到照念   /chat 切到大模型   /quit 退出
─────────────────────────────────────────────"""


def check_chat_ready():
    """chat 模式依赖 llm.py 里的 DeepSeek，缺 key 时说清楚而不是让她哑火。

    注意：真正调用大模型的是服务端进程，这里只是提前给个友好提示。
    """
    if os.getenv('DEEPSEEK_API_KEY'):
        return True, ''
    return False, ("chat 模式需要环境变量 DEEPSEEK_API_KEY。\n"
                   "  临时设置：$env:DEEPSEEK_API_KEY='sk-xxx'  然后重新运行 talk.py\n"
                   "  没有 key 就用 echo 模式，她会照念你打的字。")


def resolve_session(client, want):
    """等服务可达且有会话；--sessionid 指定时优先用它。"""
    found = client.discover_session()
    if not found:
        return None
    return want or found


def run(args):
    client = LiveTalkingClient(args.server)
    sessionid = resolve_session(client, args.sessionid)
    if not sessionid:
        print("没找到活跃会话。请先启动直播（启动直播.bat），或打开 index.html 点“开始连接”。")
        return 1

    mode = args.mode
    if mode == 'chat':
        ok, why = check_chat_ready()
        if not ok:
            print(f"[warn] {why}\n[warn] 已退回 echo 模式。\n")
            mode = 'echo'

    print(BANNER.format(mode=mode))
    print(f"已连接会话 {sessionid}\n")

    while True:
        try:
            text = input('> ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n退出互动台。数字人继续讲故事。')
            return 0

        if not text:
            continue
        if text in ('/quit', '/exit'):
            print('退出互动台。数字人继续讲故事。')
            return 0
        if text == '/echo':
            mode = 'echo'
            print('[已切到 echo：她会照念你打的字]')
            continue
        if text == '/chat':
            ok, why = check_chat_ready()
            if not ok:
                print(f'[warn] {why}')
                continue
            mode = 'chat'
            print('[已切到 chat：她会念大模型的回答]')
            continue

        try:
            if client.say(sessionid, text, mode=mode, interrupt=True):
                print(f'  ✓ 已插话（{mode}）')
            else:
                print('  ✗ 服务拒绝了这次请求')
        except ConnectionLost as e:
            print(f'  ✗ 与服务断开：{e}')
            return 1


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="数字人互动台：打字即插话")
    p.add_argument('--server', default='http://127.0.0.1:8010', help='LiveTalking 服务地址')
    p.add_argument('--sessionid', default='', help='会话 id；留空自动发现')
    p.add_argument('--mode', default='echo', choices=['echo', 'chat'], help='照念 / 大模型问答')
    return p.parse_args(argv)


if __name__ == '__main__':
    sys.exit(run(parse_args()))
