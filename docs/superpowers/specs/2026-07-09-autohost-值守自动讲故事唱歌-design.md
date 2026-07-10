# 数字人值守脚本 autohost — 设计规格

**日期**: 2026-07-09
**状态**: 已批准，待实现
**范围**: MVP —— 无人时自动循环讲故事 / 唱歌。不含聊天对话（延后）。

## 1. 目标

给已部署的 LiveTalking（wav2lip + webrtc + 温柔女声 zh-CN-XiaoxiaoNeural）加一个**外部值守脚本 `autohost.py`**：数字人空闲时，自动轮流讲故事、唱歌，循环不息。

**明确不做（YAGNI）**：
- 不接大模型聊天（用户已选延后）。
- 不做摄像头/麦克风的“有没有人”识别。
- 不修改引擎任何核心代码。

## 2. 总体方案

**外部驱动**：`autohost.py` 是独立进程，只通过 LiveTalking 已有的 HTTP API 驱动内容，与引擎零耦合。这符合项目“前端 API 对接”的扩展方式。

### 依赖的现有 API（均已存在，无需改动）
| API | 用途 |
|-----|------|
| `GET /api/admin/sessions` | 发现活跃会话，拿到 `sessionid` |
| `POST /is_speaking` | 查询数字人当前是否在说话（空闲判断的核心） |
| `POST /human`（type=echo） | 让数字人用当前 TTS 语音念一段文本（讲故事） |
| `POST /humanaudio`（multipart file） | 播放一段音频文件（唱歌，口型自动对齐） |
| `POST /interrupt_talk` | 打断当前说话（预留给未来聊天抢占） |

### 前置条件
浏览器打开 `http://127.0.0.1:8010/index.html` 并点“开始连接”，建立 WebRTC 会话后才能看到/听到数字人。`autohost.py` 驱动的是这个已连接的会话。

## 3. 运行流程

```
启动 autohost.py
  ├─ 解析命令行参数
  ├─ 构建播放清单 playlist（扫描 content/stories + content/songs）
  └─ 循环：
       1. 若无已知 sessionid → 调 /api/admin/sessions 发现一个活跃会话；没有则等待重试
       2. 调 /is_speaking：
            - 正在说话 → 记录“最后说话时间”，continue（不打断）
            - 空闲，且空闲时长 ≥ --gap 秒 → 播下一条内容：
                · 故事：按段落逐段 POST /human {type:echo, text:段落, sessionid}
                        每段之间用 /is_speaking 等它念完再发下一段
                · 歌曲：POST /humanaudio 上传整首音频文件
            - 播完 → 指针后移；到清单末尾按 --loop 决定是否重头
       3. sleep(轮询间隔，约 1s)
```

**空闲判定**：只用 `/is_speaking` 轮询。数字人静默且持续 `--gap` 秒未说话 → 视为“该播下一条了”。若将来有人打字触发对话，数字人会说话，脚本自然暂停内容，对话结束静默后再恢复——同一套逻辑，无需重构。

## 4. 内容组织

```
content/
  stories/    # 故事文本，每个 .txt 一篇；用空行分段，脚本按段逐段念
  songs/      # 歌曲音频 .wav/.mp3；为空则清单只含故事
```

- 项目内先放 2~3 篇温柔中文小故事作为示例，用户可自由增删。
- 歌曲音频由用户自行放入 `content/songs/`（TTS 不擅长唱歌，唱歌走 `/humanaudio` 播放预制音频）。
- 播放清单由两个目录的文件构成，条目类型为 `story`（文本，按段念）或 `song`（音频，整首播）。

## 5. 命令行参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--server` | `http://127.0.0.1:8010` | LiveTalking 服务地址 |
| `--sessionid` | 空（自动发现） | 指定会话 ID；不填则自动取第一个活跃会话 |
| `--gap` | `4` | 静默多少秒后开始播下一条 |
| `--order` | `sequential` | `sequential`(顺序) / `random`(随机) / `alternate`(故事歌曲交替) |
| `--loop` | `true` | 播完整个清单后是否重头循环 |
| `--content` | `./content` | 内容根目录 |

## 6. 组件划分

`autohost.py` 内部分成职责清晰的小单元：

- **content loader**：扫描 `content/`，产出播放清单（story/song 条目）。输入=目录路径，输出=条目列表。
- **playlist iterator**：按 `--order` / `--loop` 给出下一条。纯逻辑，可独立测试。
- **LiveTalking client**：封装对上述 5 个 HTTP API 的调用（发现会话、查说话状态、发文本、传音频）。输入=参数，输出=响应；网络错误就地捕获重试。
- **driver loop**：编排上述三者的主循环（空闲判定 + 播下一条 + 停顿）。

每个单元可单独理解和测试；`content loader` 与 `playlist iterator` 是纯函数式逻辑，不依赖网络。

## 7. 错误处理

- 找不到活跃会话：打印提示“请先在浏览器打开 index.html 并点开始连接”，等待重试，不崩溃。
- 服务未启动 / 网络错误：捕获异常、打印、退避重试，不崩溃。
- `content/` 为空或无故事：明确报错并退出（没内容可播）。
- 歌曲目录为空：正常，清单只含故事。
- 音频文件读取失败：跳过该条，日志记录，继续下一条。

## 8. 测试策略

- **content loader / playlist iterator**：纯逻辑，单元测试（构造临时目录/条目列表，断言清单与顺序，含 sequential/random/alternate、loop 边界）。
- **LiveTalking client**：对着真实本地服务做冒烟——发现会话、echo 念一句、传一段音频，人工确认数字人有反应。
- **driver loop**：端到端手动验收——浏览器连上后运行脚本，观察空闲时自动讲故事、（放了歌曲则）唱歌、循环不断。

## 9. 验收标准

1. 浏览器连接后运行 `python autohost.py`，数字人空闲几秒即开始用温柔女声念故事。
2. 一篇念完停顿后自动播下一条（故事或歌曲）。
3. 放入 `content/songs/` 的音频会被播放且口型对齐。
4. 清单播完后按 `--loop` 重头循环，长期不间断。
5. 服务未启动 / 未连接会话时，脚本给出清晰提示而非报错崩溃。
6. 全程未修改引擎任何文件。
