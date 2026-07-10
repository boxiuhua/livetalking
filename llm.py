import json
import time
import os
from collections import deque
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from avatars.base_avatar import BaseAvatar
from utils.logger import logger

# 数字人的人设。她的话会直接进 TTS，所以必须禁掉 emoji 和 markdown——
# 否则 "*温柔地*" 和 "😊" 会被一个字一个字念出来。
SYSTEM_PROMPT = (
    "你是一位温柔的直播间女主播，声音轻柔，说话像在跟朋友聊天。"
    "回答要简短，一般两三句话就够，口语化，不要书面语。"
    "绝对不要使用 emoji、颜文字、markdown 星号井号、括号里的动作描写或任何符号，"
    "因为你的回答会被直接朗读出来。只输出要说出口的话。"
)

# 记住最近 6 轮对话（12 条消息）。再多会拖慢首字延迟，直播场景也用不上更长的上文。
MAX_HISTORY_MESSAGES = 12


NOW_PLAYING = os.path.join('runtime', 'now_playing.json')


def _now_playing_hint():
    """autohost 讲的故事走 echo，不经过大模型。不告诉它当前节目，
    观众一问“你刚才讲的是什么”，它就会编一个。"""
    try:
        with open(NOW_PLAYING, encoding='utf-8') as f:
            st = json.load(f)
    except Exception:
        return ''
    if st.get('kind') == 'story':
        return (f"\n你现在正在直播间给大家讲一个故事，名字叫《{st['title']}》，全文如下：\n"
                f"{st.get('text', '')}\n"
                "观众问起这个故事时，只能依据上面的原文回答。"
                "原文里没有的人物和情节，绝对不要编造，不知道就说这个故事里没提到。")
    if st.get('kind') == 'song':
        return f"\n你现在正在直播间唱一首歌，名字叫《{st['title']}》。"
    return ''


def _history(avatar_session):
    """每个会话一条独立的对话历史，挂在 session 对象上，随会话一起消失。"""
    h = getattr(avatar_session, '_chat_history', None)
    if h is None:
        h = deque(maxlen=MAX_HISTORY_MESSAGES)
        avatar_session._chat_history = h
    return h


def reset_history(avatar_session):
    """清空上下文，让她重新开始一段对话。"""
    _history(avatar_session).clear()


def llm_response(message,avatar_session:'BaseAvatar',datainfo:dict={}):
    try:
        opt = avatar_session.opt
        start = time.perf_counter()
        from openai import OpenAI
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            logger.error("DEEPSEEK_API_KEY 未设置，chat 模式无法回答。")
            return
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        end = time.perf_counter()
        logger.info(f"llm Time init: {end-start}s,{message}")
        history = _history(avatar_session)
        system = SYSTEM_PROMPT + _now_playing_hint()
        completion = client.chat.completions.create(
            model="deepseek-chat",
            messages=([{'role': 'system', 'content': system}]
                      + list(history)
                      + [{'role': 'user', 'content': message}]),
            stream=True,
            # 通过以下设置，在流式输出的最后一行展示token使用信息
            stream_options={"include_usage": True}
        )
        result=""
        full=""       # 攒完整回复，存进历史；result 会被逐句清空，攒不到全文
        first = True
        for chunk in completion:
            if len(chunk.choices)>0:
                #print(chunk.choices[0].delta.content)
                if first:
                    end = time.perf_counter()
                    logger.info(f"llm Time to first chunk: {end-start}s")
                    first = False
                msg = chunk.choices[0].delta.content
                if msg is None:
                    continue
                full += msg
                lastpos=0
                #msglist = re.split('[,.!;:，。！?]',msg)
                for i, char in enumerate(msg):
                    if char in ",.!;:，。！？：；" :
                        result = result+msg[lastpos:i+1]
                        lastpos = i+1
                        if len(result)>10:
                            logger.info(result)
                            avatar_session.put_msg_txt(result,datainfo)
                            result=""
                result = result+msg[lastpos:]
        end = time.perf_counter()
        logger.info(f"llm Time to last chunk: {end-start}s")
        if result:
            avatar_session.put_msg_txt(result,datainfo)

        # 一问一答都进历史。生成失败时两条都不进，免得留下一个没人回答的提问。
        if full:
            history.append({'role': 'user', 'content': message})
            history.append({'role': 'assistant', 'content': full})
            logger.info(f"llm history: {len(history)} 条消息")

    except Exception as e:
        logger.exception('llm exceptiopn:')
        return   