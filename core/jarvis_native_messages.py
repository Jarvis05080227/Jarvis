# -*- coding: utf-8 -*-
"""[BYE-NATIVE-1 / 2026-09-04 Sir 令「开工」] 主脑的 Anthropic 原生 /v1/messages 流式腿。

## 为什么有这个模块

09-04 实测: 中转 (byecore 0.65 Claude-Max 槽) 的 **OpenAI 壳 `/chat/completions`
写缓存照收、读侧恒 0**。2×2 矩阵 (同一把 key / 同一个池 / 同一个模型 /
逐字节相同的稳定前缀):

    端点                      前缀放 system     前缀放 user part
    原生 /v1/messages         写3116→读3116 ✓   写3207→读3207 ✓
    OpenAI /chat/completions  0/3 发           0/12 发

**端点是唯一变量** —— 不是我们的 prompt 形, 不是断点数, 不是尺寸 (10K 的固定
前缀一样吃不到)。09-02 中午壳还好 (真账: pt=47,140 → cached=31,702), 当天下午
起全 0 = 中转侧的回归。CPA 当翻译也不行: OpenAI 进 CPA 那层每发多写 23 tok,
cloak never / always+cache-user-id 两档都是 0/4 —— 病形与本仓 CPA config 头注
②「cloak 每发注入随机 user_id → 前缀被打散 → 命中 0%」同族。

## 边界 (Sir 09-04 拍「装配留着, 只是把外面的方式改了」)

  · **装配一行不动** —— messages / tools / 缓存断点 / extra_body 全在
    `create_chat_stream` 之前算完, 本模块只做「方言翻译 + 流式读」。
  · **消费侧一行不动** —— 吐出去的 chunk 与 OpenAI SDK 的流 duck-type 同形:
    `chunk.choices[0].delta.content` / `.delta.tool_calls` / `.finish_reason`
    / `chunk.usage`。NativeToolCallAccumulator 收 dict 形 tool_calls, 已验。
  · **只在 profile 翻一位才走本路** (`dialect: anthropic_native`); 不带这位 =
    逐字节旧形, 零行为差。
  · 异常一律抛 **openai SDK 的异常类**, 不自立门户 —— 建流期/中途断的所有
    既有判据 (is_transient_create_error 只认 status_code / 403 退避 / 断路器
    classify_failure / BYECORE-400-HEAL) 才能原样继续工作。

## 抄的是谁

网关 08-21 那份 `_byecore_native_sse_collect` (BYECORE-STREAM-1): 事件表、
半截判据、usage 映射、cap 的定标全同源, 连「中转原生端点不守 max_tokens」这条
都是那边先吃过的亏 (RELAY-GEN-CAP-1: 08-24 一发 19,731 tok / $1.08)。
**唯一的差别**: 那份攒完再装回非流式 dict, 主脑不能攒 (Sir 要逐字出声),
所以这里是 generator, 边收边吐。

## 两道 cap 各管各的

  · 可见正文的 cap 归**消费侧** `_llmt.relay_gen_cap_chars` (RELAY-GEN-CAP-1,
    32000 字符, 判据认 profile 名里的 'byecore') —— 本模块不重复设, 免得两把尺
    在同一段话上互相截 (长度政策只该有一处)。
  · 本模块只补消费侧看不见的那一半: **正文 + 推理**的总生成量。thinking_delta
    不进正文 (与网关同), 消费侧的尺数不到它, 而账单数得到 —— 网关那边的探针
    实证「给 max_tokens=8 也出 6979 output tokens」。定标不取绝对值而是跟着
    本发自己的 max_tokens 走 (× GEN_CAP_FACTOR): 服务端被告知了 N, 我们在 1.5N
    处停手。常态一次都不该咬到, 咬到即 loud 一行。
"""

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional

import httpx

# 本模块顶层禁 import jarvis_utils (与 jarvis_llm_transport 同一条防环家法),
# 需要落日志的地方 lazy import。
_CHARS_PER_TOKEN = 4          # 与网关 _BYECORE_NATIVE_CHARS_PER_TOKEN 同刻度
_GEN_CAP_FACTOR = 1.5         # 总生成量天花板 = max_tokens × 本系数
_GEN_CAP_SLACK_CHARS = 2000   # 小 max_tokens 时别把余量算没了

# OpenRouter 专用键: 原生端点不认, 带过去轻则被忽略重则 400。
# provider = CACHE-PIN / GPIN-MB 的钉子 (只对 OpenRouter 路由层有意义);
# reasoning = MB-SONNET5-S1 那把 (原生的对应物是 thinking, 由 extra_body 另给)。
_OPENROUTER_ONLY_KEYS = ('provider', 'reasoning', 'transforms', 'route',
                         'models', 'plugins', 'include_reasoning')

# stop_reason → OpenAI finish_reason。
# tool_use → 'tool_calls' 是硬要求: 消费侧 (_stream_wrapper) 正是拿
# finish_reason == 'tool_calls' 决定要不要把累积到的原生调用合成 FAST_CALL 文本。
# refusal → 'content_filter': 与 CPA-FABLE51-REFUSAL 那条判据的口径对齐
# (5.1 拒答时 finish_reason='content_filter' + 零产出 + 带 usage)。
_STOP_MAP = {
    'end_turn': 'stop',
    'stop_sequence': 'stop',
    'max_tokens': 'length',
    'tool_use': 'tool_calls',
    'refusal': 'content_filter',
    'pause_turn': 'stop',
}


def _bg_log(msg: str, audience: str = '') -> None:
    try:
        import jarvis_utils as _ju
        if audience:
            _ju.bg_log(msg, audience=audience)
        else:
            _ju.bg_log(msg)
    except Exception:
        pass


def native_messages_url(base: str) -> str:
    """OpenAI 方言 base (…/v1) → 原生 messages 端点 (…/v1/messages)。

    与网关 `_byecore_native_url` 逐字节同形 —— 同一件事只该有一种算法。
    """
    b = str(base or '').rstrip('/')
    if b.endswith('/v1'):
        return b + '/messages'
    return b + '/v1/messages'


# ---------------------------------------------------------------------------
# 请求体: OpenAI 方言 → Anthropic 原生
# ---------------------------------------------------------------------------

def _image_block_from_data_url(url: str) -> Optional[Dict[str, Any]]:
    """`data:image/jpeg;base64,xxx` → 原生 image block。非 data URL → None。

    原生端点只收 base64/url source; 主脑这条路的图恒是 data URL
    (chat_bypass :915 由 inline_data 现拼), 拼不出来就返 None 让上面丢掉 —
    宁可少一张图也不要整发 400 (与模态守门同极性)。
    """
    try:
        s = str(url or '')
        if not s.startswith('data:'):
            return None
        head, b64 = s.split(',', 1)
        mime = head[5:].split(';', 1)[0] or 'image/jpeg'
        if not b64:
            return None
        return {'type': 'image',
                'source': {'type': 'base64', 'media_type': mime, 'data': b64}}
    except Exception:
        return None


def _convert_parts(parts: Any) -> List[Dict[str, Any]]:
    """OpenAI content parts → 原生 content blocks。**cache_control 原样带过去**
    (整条腿的意义就在这一句上)。"""
    out: List[Dict[str, Any]] = []
    if isinstance(parts, str):
        if parts:
            out.append({'type': 'text', 'text': parts})
        return out
    if not isinstance(parts, list):
        return out
    for p in parts:
        if not isinstance(p, dict):
            continue
        t = p.get('type')
        if t == 'text':
            txt = p.get('text')
            if not isinstance(txt, str) or not txt:
                continue
            blk: Dict[str, Any] = {'type': 'text', 'text': txt}
            if p.get('cache_control'):
                blk['cache_control'] = p['cache_control']
            out.append(blk)
        elif t == 'image_url':
            src = (p.get('image_url') or {}).get('url') if isinstance(
                p.get('image_url'), dict) else None
            blk_i = _image_block_from_data_url(src or '')
            if blk_i:
                if p.get('cache_control'):
                    blk_i['cache_control'] = p['cache_control']
                out.append(blk_i)
        # input_audio: 原生 Anthropic 无音频入口。走到这里说明模态守门
        # (_strip_unsupported_modalities) 漏了 —— 丢掉并留一行, 别静默。
        elif t == 'input_audio':
            _bg_log('⚠️ [NativeLeg] 音频 part 到了原生腿 (Anthropic 无此入口) '
                    '→ 已丢; 模态守门本该更早剥掉它', audience='diag')
    return out


def build_native_body(model: str,
                      messages: List[Dict[str, Any]],
                      temperature: Any = None,
                      max_tokens: Any = None,
                      tools: Any = None,
                      parallel_tool_calls: Any = None,
                      extra_body: Any = None,
                      stream: bool = True) -> Dict[str, Any]:
    """把主脑那份 OpenAI 形请求翻成原生 /v1/messages 体。纯函数, 不发网络。

    · 打头的 system 消息 → 顶层 `system` 数组 (cache_control 随行)。真账实测
      主脑恒是「system 一条打头 + user/model 交替」(main_stream_corpus 55 段
      1 个 system), 所以这条路走得通; 万一 system 出现在中途, 不静默丢 ——
      降级成 user 消息保内容, 并留一行 (少一块记忆比多一条 400 更难查)。
    · tools: OpenAI function 形 → 原生 {name, description, input_schema}。
    · parallel_tool_calls=False → tool_choice.disable_parallel_tool_use
      (FAST_CALL 契约是一次一调)。
    · extra_body: thinking / output_config 是这条腿的原生方言 (与网关
      _byecore_effort_dialect 同形), 原样并入; OpenRouter 专用键剥掉。
    """
    system_blocks: List[Dict[str, Any]] = []
    native_msgs: List[Dict[str, Any]] = []
    seen_non_system = False
    for m in (messages or []):
        if not isinstance(m, dict):
            continue
        role = str(m.get('role') or 'user')
        blocks = _convert_parts(m.get('content'))
        if not blocks:
            continue                       # 空 content 不出线 (P0-TOOLCONT-EMPTY-1 同极性)
        if role == 'system':
            if not seen_non_system:
                system_blocks.extend(blocks)
                continue
            _bg_log('⚠️ [NativeLeg] system 消息出现在对话中途 → 降级成 user '
                    '消息发出 (内容不丢)', audience='diag')
            role = 'user'
        seen_non_system = True
        native_msgs.append({'role': 'assistant' if role == 'assistant' else 'user',
                            'content': blocks})

    body: Dict[str, Any] = {'model': str(model or ''), 'messages': native_msgs}
    if system_blocks:
        body['system'] = system_blocks
    if max_tokens is not None:
        body['max_tokens'] = int(max_tokens)
    if temperature is not None:
        body['temperature'] = float(temperature)
    if stream:
        body['stream'] = True

    if tools:
        nat_tools = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get('function') if t.get('type') == 'function' else None
            if not isinstance(fn, dict):
                continue
            nat_tools.append({
                'name': fn.get('name'),
                'description': fn.get('description') or '',
                'input_schema': fn.get('parameters') or {
                    'type': 'object', 'properties': {}},
            })
        if nat_tools:
            body['tools'] = nat_tools
            if parallel_tool_calls is False:
                body['tool_choice'] = {'type': 'auto',
                                       'disable_parallel_tool_use': True}

    if isinstance(extra_body, dict):
        for k, v in extra_body.items():
            if str(k).startswith('_') or k in _OPENROUTER_ONLY_KEYS:
                continue
            body[k] = v
    return body


# ---------------------------------------------------------------------------
# 吐出去的 chunk: 与 OpenAI SDK 流 duck-type 同形
# ---------------------------------------------------------------------------

class _Delta(object):
    __slots__ = ('content', 'tool_calls', 'role', 'reasoning')

    def __init__(self, content=None, tool_calls=None, role=None):
        self.content = content
        self.tool_calls = tool_calls
        self.role = role
        self.reasoning = None        # mirror tap 只在实验路读, 生产恒 None


class _Choice(object):
    # [REFUSAL-LOG-1 / 2026-09-08] stop_details 是原生独有的一格 (OpenAI 壳没有
    # 这个字段, CPA 二进制里连这个串都不存在) — 挂在 choice 上属于加字段不改形,
    # 消费侧 getattr 取不到就是 None, duck-type 契约不动。
    __slots__ = ('delta', 'finish_reason', 'index', 'stop_details')

    def __init__(self, delta=None, finish_reason=None, index=0,
                 stop_details=None):
        self.delta = delta if delta is not None else _Delta()
        self.finish_reason = finish_reason
        self.index = index
        self.stop_details = stop_details


class _Chunk(object):
    __slots__ = ('choices', 'usage', 'model', 'id')

    def __init__(self, choices=None, usage=None, model='', cid=''):
        self.choices = choices if choices is not None else []
        self.usage = usage
        self.model = model
        self.id = cid


# ---------------------------------------------------------------------------
# 异常: 一律用 openai SDK 的类 (下游判据全靠它们的 status_code / 类型名)
# ---------------------------------------------------------------------------

def _raise_status(resp: httpx.Response, text: str) -> None:
    """非 200 → 抛与 SDK 同类同形的异常 (message 也照 SDK 的
    `Error code: NNN - <body>` 拼, BYECORE-400-HEAL 那类字面判据才认得)。"""
    import openai as _oa
    msg = 'Error code: %d - %s' % (resp.status_code, text)
    body = None
    try:
        body = json.loads(text)
    except Exception:
        body = {'message': text}
    sc = resp.status_code
    cls = {400: _oa.BadRequestError, 401: _oa.AuthenticationError,
           403: _oa.PermissionDeniedError, 404: _oa.NotFoundError,
           409: _oa.ConflictError, 422: _oa.UnprocessableEntityError,
           429: _oa.RateLimitError}.get(sc)
    if cls is None:
        cls = _oa.InternalServerError if sc >= 500 else _oa.APIStatusError
    try:
        raise cls(msg, response=resp, body=body)
    except TypeError:
        # SDK 大版本变了构造签名 → 退到基类, 但 status_code 必须还在
        # (is_transient_create_error 只认它)
        err = _oa.APIStatusError(msg, response=resp, body=body)
        raise err


def _wrap_transport_error(exc: Exception, request: Any):
    """httpx 传输层异常 → SDK 的连接/超时族 (瞬断判据认这两类)。"""
    import openai as _oa
    if isinstance(exc, httpx.TimeoutException):
        return _oa.APITimeoutError(request=request)
    # evidcut-ok: 传输层异常串进 exception message → 日志/降档判据, 不进 prompt
    return _oa.APIConnectionError(message=str(exc)[:200], request=request)


# ---------------------------------------------------------------------------
# 连接复用: 每个 base_url 一个 httpx.Client (TLS 握手 2.3-3.9s vs 复用 0.35s,
# 量具见 CPA-KEEPALIVE-1)。与 chat_bypass._get_or_client 同一个动机。
# ---------------------------------------------------------------------------
_CLIENTS: Dict[str, httpx.Client] = {}


def _get_client(base_url: str, timeout: Any, proxy: str = '') -> httpx.Client:
    """[APICREDIT-1-A] proxy 非空 = 钉死出口: httpx 走它且 trust_env=False
    (不读 HTTPS_PROXY / JARVIS_PROXY_URL_OVERRIDE, 活体那条是 7897 日本节点)。
    缓存键带上 proxy, 免得同 base_url 的两种出口共用一个连接池。
    proxy 空 = 逐字节旧形 (键 = base_url, 构造参数不变)。"""
    proxy = str(proxy or '').strip()
    key = str(base_url or '') if not proxy else (
        str(base_url or '') + '|egress=' + proxy)
    cli = _CLIENTS.get(key)
    if cli is not None and not cli.is_closed:
        return cli
    _tmo = timeout if timeout is not None else httpx.Timeout(
        connect=10.0, read=185.0, write=10.0, pool=10.0)
    if proxy:
        cli = httpx.Client(timeout=_tmo, proxy=proxy, trust_env=False)
    else:
        cli = httpx.Client(timeout=_tmo)
    _CLIENTS[key] = cli
    return cli


# [APICREDIT-1-A / 2026-10-09 Sir 令「全部调用只走 7890 固定出口; 代理不通 =
# 报错不直连」] 官方 API 端点: profile 不写 egress_proxy 就拒建流 (不发请求),
# 码里没有任何直连回落分支。判据按 host (base_url 指向官方即受管), 与 profile
# 名无关 —— 名字会骗人, host 不会。
_PINNED_EGRESS_HOSTS = ('api.anthropic.com',)


def egress_required(base_url: str) -> bool:
    """base_url 指向官方 API (须钉出口) → True。
    子串判 (不靠 URL 解析): 解析失败也不会漏成「不受管」= fail-toward-拦。"""
    low = str(base_url or '').lower()
    return any(h in low for h in _PINNED_EGRESS_HOSTS)


# [APICREDIT-1-A2 / 2026-10-09] 钉出口官方腿的缓存时长: 主脑断点不写 ttl
# (prompt_cache long_ttl_breakpoints=0; 尾断点 B1 闸 0 也不写) —— CPA 路由
# CPA 在出口把无 ttl 断点补成 1h (upgradeClaudeCacheControlTTL), 原生直连
# 官方没人补 → 官方默认 5m, 而主脑两轮间隔常过 5m (V1 两发隔 437s:
# 第二发 cached=0 cache_write=162899, 整段重写)。保活按 A 设计不给钉出口腿
# 发。本函数在出网前一刻对**本次发送体**补 ttl='1h', 形同 CPA: 已写 ttl 的
# 一律不动; 官方规则「1h 断点须在 5m 之前」(序 tools → system → messages)
# —— 一旦前面已有 5m (显式, 或因此规则留着没补的), 其后无 ttl 的不补, 留一行。
# copy-on-write: 换掉的是 body 里的 list/dict, 不写回调用方的 messages 对象
# (_convert_parts 原样搬的 cache_control dict 与上游共享引用)。
def _cc_upgrade_list(items: Any, state: Dict[str, int]) -> Any:
    if not isinstance(items, list):
        return items
    new_items = None
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        cc = it.get('cache_control')
        if not isinstance(cc, dict):
            continue
        ttl = str(cc.get('ttl') or '').strip()
        if ttl:
            if ttl != '1h':
                state['seen_short'] = 1
            continue
        if state.get('seen_short'):
            state['skipped'] = state.get('skipped', 0) + 1
            continue
        if new_items is None:
            new_items = list(items)
        new_items[i] = dict(it, cache_control=dict(cc, ttl='1h'))
        state['upgraded'] = state.get('upgraded', 0) + 1
    return items if new_items is None else new_items


def upgrade_cache_ttl_1h(body: Dict[str, Any]) -> Dict[str, Any]:
    """无 ttl 的 cache_control → ttl='1h' (发送体副本上改, 不碰上游对象)。
    返回新 body (无可补则原对象)。只由 create_native_stream 对钉出口官方腿调。"""
    if not isinstance(body, dict):
        return body
    state: Dict[str, int] = {}
    out = dict(body)
    for key in ('tools', 'system'):
        if key in out:
            out[key] = _cc_upgrade_list(out[key], state)
    msgs = out.get('messages')
    if isinstance(msgs, list):
        new_msgs = None
        for i, m in enumerate(msgs):
            if not isinstance(m, dict):
                continue
            c0 = m.get('content')
            c1 = _cc_upgrade_list(c0, state)
            if c1 is not c0:
                if new_msgs is None:
                    new_msgs = list(msgs)
                new_msgs[i] = dict(m, content=c1)
        if new_msgs is not None:
            out['messages'] = new_msgs
    top = out.get('cache_control')          # 顶层自动缓存 (若有) 落在最后
    if isinstance(top, dict) and not str(top.get('ttl') or '').strip():
        if state.get('seen_short'):
            state['skipped'] = state.get('skipped', 0) + 1
        else:
            out['cache_control'] = dict(top, ttl='1h')
            state['upgraded'] = state.get('upgraded', 0) + 1
    if state.get('skipped'):
        _bg_log('⚠️ [NativeLeg] 缓存 ttl 补 1h: %d 个无 ttl 断点在 5m 断点之后, '
                '按官方次序规则 (1h 须在 5m 前) 不补, 留默认 5m (已补 %d 个)'
                % (state['skipped'], state.get('upgraded', 0)), audience='diag')
    if not state.get('upgraded'):
        return body
    return out


def _gen_cap_chars(max_tokens: Any) -> int:
    """本发的总生成量 (正文+推理) 天花板, 字符。0 = 不设。"""
    try:
        env = os.environ.get('JARVIS_NATIVE_GEN_CAP_CHARS')
        if env:
            return max(0, int(env))
        mt = int(max_tokens or 0)
        if mt <= 0:
            return 0
        return int(mt * _CHARS_PER_TOKEN * _GEN_CAP_FACTOR) + _GEN_CAP_SLACK_CHARS
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 建流 + 流式读
# ---------------------------------------------------------------------------

def create_native_stream(leg: Dict[str, Any], attempt: Any = None,
                         supply=None, **kwargs) -> Iterator[Any]:
    """建原生流并返回 chunk 迭代器。**建流期的错在本函数内抛**, 不留到迭代时。

    这条契约是抄 SDK 的: chat_bypass 的 403 退避、MBATT-1 同腿重试、断路器
    note_leg_error 全都建立在「.create() 抛 = 建流失败, 迭代抛 = 中途断」这条
    分界上 (:1117 的注写得明明白白)。generator 的函数体要到第一次 next() 才跑,
    所以这里先 send 拿到响应头判完状态码, 再把剩下的交给内层 generator。
    """
    base_url = str(leg.get('base_url') or '')
    api_key = str(leg.get('api_key') or '')
    url = native_messages_url(base_url)
    tmo = (attempt or {}).get('timeout') or leg.get('timeout')

    # [APICREDIT-1-A / B3] drop_sampling: 官方 Fable 5/5.1 已移除采样参数,
    # 发 temperature 即 400; 主脑恒传 0.7 (chat_bypass)。只在 profile 声明时剥,
    # 不声明 = 逐字节旧形 (CPA/中转腿照传)。
    _temp = None if leg.get('drop_sampling') else kwargs.get('temperature')
    body = build_native_body(
        model=kwargs.get('model') or '',
        messages=kwargs.get('messages') or [],
        temperature=_temp,
        max_tokens=kwargs.get('max_tokens'),
        tools=kwargs.get('tools'),
        parallel_tool_calls=kwargs.get('parallel_tool_calls'),
        extra_body=kwargs.get('extra_body'),
        stream=True)

    headers = {'Content-Type': 'application/json',
               'anthropic-version': '2023-06-01',
               'x-api-key': api_key,
               'Authorization': 'Bearer ' + api_key}
    # [APICREDIT-1-A / B2] auth_header=x-api-key: 官方端点上 Bearer 认的是
    # OAuth token, 两头同带有 401 风险 → 只发 x-api-key。不声明 = 双头旧形。
    if str(leg.get('auth_header') or '').strip().lower() == 'x-api-key':
        headers.pop('Authorization', None)
    # profile 的 default_headers (中转那把 User-Agent: 缺它被 Cloudflare 边缘
    # 1010 挡在鉴权之前 —— FABLE5-KNOB-401-FIX 判例) 必须带上。
    for k, v in (leg.get('headers') or {}).items():
        headers[str(k)] = str(v)

    # 空 key 在 httpx 里会炸成 `Illegal header value b'Bearer '` —— 一句看不出
    # 病因的传输层错。生产路上 chat_bypass 解不出 key 时早就整体回落 openrouter
    # 了 (MAINBRAIN-SEAM-P2 红线), 走到这里说明是别的路进来的; 明说比装死好。
    if not api_key or not base_url:
        import openai as _oa
        raise _oa.APIConnectionError(
            message='native leg 缺 %s (profile=%s)' % (
                'api_key' if not api_key else 'base_url',
                leg.get('profile_name') or '?'),
            request=None)

    # [APICREDIT-1-A / B1] 出口钉死: 官方端点没声明 egress_proxy → 拒建流
    # (一个字节都不发); 声明了 → 客户端只走它, 不读环境代理。代理不通 =
    # httpx 连接错 → APIConnectionError, 没有直连回落。
    _egress = str(leg.get('egress_proxy') or '').strip()
    if not _egress and egress_required(base_url):
        import openai as _oa
        raise _oa.APIConnectionError(
            message='native leg 官方端点未钉 egress_proxy, 拒发 (profile=%s)'
                    % (leg.get('profile_name') or '?'),
            request=None)

    # [APICREDIT-1-A2] 钉出口官方腿 (判据同上: egress_proxy 声明 + 官方 host)
    # 出网前补 1h ttl; 其余腿 (CPA / 中转原生口) 逐字节旧形。
    _pinned_official = bool(_egress) and egress_required(base_url)
    if _pinned_official:
        body = upgrade_cache_ttl_1h(body)

    cli = _get_client(base_url, tmo, proxy=_egress)
    from jarvis_desk_supply import send as _supply_send
    _supply_send(supply, body, 'native-messages')
    req = cli.build_request('POST', url, json=body, headers=headers,
                            timeout=tmo if tmo is not None else None)
    try:
        resp = cli.send(req, stream=True)
    except Exception as e:
        raise _wrap_transport_error(e, req)
    if resp.status_code != 200:
        try:
            text = resp.read().decode('utf-8', 'replace')[:2000]
        except Exception:
            text = ''
        finally:
            try:
                resp.close()
            except Exception:
                pass
        _raise_status(resp, text)
    return _iter_native_stream(resp, req, int(kwargs.get('max_tokens') or 0),
                               str(leg.get('profile_name') or ''),
                               cache_split=_pinned_official)


def _iter_native_stream(resp: httpx.Response, request: Any, max_tokens: int,
                        profile_name: str,
                        cache_split: bool = False) -> Iterator[Any]:
    """SSE 事件 → OpenAI 形 chunk。事件表与网关 _byecore_native_sse_collect 同源。

    · message_start        → usage 的 input / cache 两数 (先落, 末 chunk 一起给)
    · content_block_start  → tool_use 起头 (id/name) → 一个 tool_calls delta
    · content_block_delta  → text_delta 进正文 yield; input_json_delta 续 arguments;
                             thinking_delta 不进正文 (与网关同) 但**要计数**
    · message_delta        → stop_reason + output_tokens
    · message_stop         → 收尾
    · error                → 抛 (带上已收字数, 便于事后对账)
    无 stop_reason 且无 message_stop = 半截 → 抛 (异常串带 'incomplete',
    落在 _TRANSIENT_BREAK_MARKS 上 → 零产出时消费侧会原腿重建一次)。
    """
    in_tok = 0
    out_tok = 0
    cache_read = 0
    cache_write = 0
    cache_creation: Optional[Dict[str, int]] = None   # [A2] 5m/1h 分项
    stop = None
    model = ''
    msg_id = ''
    saw_stop = False
    stop_details = None            # [REFUSAL-LOG-1] 拒答类别 (只有原生带)
    n_text = 0
    n_gen = 0                      # 正文 + 推理 (账单看得见的那个量)
    gen_cap = _gen_cap_chars(max_tokens)
    gen_capped = False
    tool_idx_by_block: Dict[int, int] = {}
    next_tool_idx = 0

    def _take_usage(u: Any) -> None:
        nonlocal in_tok, out_tok, cache_read, cache_write, cache_creation
        if not isinstance(u, dict):
            return
        if u.get('input_tokens') is not None:
            in_tok = int(u.get('input_tokens') or 0)
        if u.get('output_tokens') is not None:
            out_tok = int(u.get('output_tokens') or 0)
        if u.get('cache_read_input_tokens') is not None:
            cache_read = int(u.get('cache_read_input_tokens') or 0)
        if u.get('cache_creation_input_tokens') is not None:
            cache_write = int(u.get('cache_creation_input_tokens') or 0)
        _cc = u.get('cache_creation')
        if cache_split and isinstance(_cc, dict):
            try:
                cache_creation = {
                    k: int(_cc.get(k) or 0)
                    for k in ('ephemeral_5m_input_tokens',
                              'ephemeral_1h_input_tokens')
                    if _cc.get(k) is not None}
            except Exception:
                pass

    try:
        try:
            line_iter = resp.iter_lines()
        except Exception as e0:
            raise _wrap_transport_error(e0, request)
        while True:
            try:
                raw = next(line_iter)
            except StopIteration:
                break
            except Exception as e1:
                raise _wrap_transport_error(e1, request)
            if not raw:
                continue
            line = raw.decode('utf-8', 'replace') if isinstance(
                raw, bytes) else str(raw)
            line = line.strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if not payload or payload == '[DONE]':
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue                    # 心跳 / 注释行
            if not isinstance(obj, dict):
                continue
            et = str(obj.get('type') or '')

            if et == 'message_start':
                msg = obj.get('message') or {}
                if isinstance(msg, dict):
                    model = str(msg.get('model') or model)
                    msg_id = str(msg.get('id') or msg_id)
                    _take_usage(msg.get('usage'))

            elif et == 'content_block_start':
                blk = obj.get('content_block') or {}
                if isinstance(blk, dict) and blk.get('type') == 'tool_use':
                    bi = int(obj.get('index') or 0)
                    tool_idx_by_block[bi] = next_tool_idx
                    tc = [{'index': next_tool_idx,
                           'id': str(blk.get('id') or ''),
                           'type': 'function',
                           'function': {'name': str(blk.get('name') or ''),
                                        'arguments': ''}}]
                    next_tool_idx += 1
                    yield _Chunk([_Choice(_Delta(tool_calls=tc))],
                                 model=model, cid=msg_id)

            elif et == 'content_block_delta':
                d = obj.get('delta') or {}
                if not isinstance(d, dict):
                    continue
                dt = d.get('type')
                if dt == 'thinking_delta':
                    th = d.get('thinking')
                    if not isinstance(th, str):
                        th = d.get('text')
                    if isinstance(th, str) and th:
                        n_gen += len(th)
                        if gen_cap and n_gen >= gen_cap:
                            gen_capped = True
                            stop = 'max_tokens'
                            break
                elif dt == 'text_delta':
                    t = d.get('text')
                    if isinstance(t, str) and t:
                        n_text += len(t)
                        n_gen += len(t)
                        if gen_cap and n_gen >= gen_cap:
                            over = n_gen - gen_cap
                            if over > 0:
                                # evidcut-ok: 生成侧失控护栏, 切的是本发自己的输出流
                                t = t[:max(0, len(t) - over)]
                            gen_capped = True
                            stop = 'max_tokens'
                            if t:
                                yield _Chunk([_Choice(_Delta(content=t))],
                                             model=model, cid=msg_id)
                            break
                        yield _Chunk([_Choice(_Delta(content=t))],
                                     model=model, cid=msg_id)
                elif dt == 'input_json_delta':
                    pj = d.get('partial_json')
                    if isinstance(pj, str) and pj:
                        bi = int(obj.get('index') or 0)
                        ti = tool_idx_by_block.get(bi, 0)
                        tc = [{'index': ti, 'type': 'function',
                               'function': {'arguments': pj}}]
                        yield _Chunk([_Choice(_Delta(tool_calls=tc))],
                                     model=model, cid=msg_id)

            elif et == 'message_delta':
                d = obj.get('delta') or {}
                if isinstance(d, dict) and d.get('stop_reason'):
                    stop = str(d.get('stop_reason'))
                # [REFUSAL-LOG-1 / 2026-09-08] stop_details 与 stop_reason 同住
                # 这一个 delta 里。原形只取前者 → 线上只见 finish=content_filter,
                # 「是哪一类拒答」从 09-02 到 09-08 无人能答 (真回执一直带着:
                # {'type':'refusal','category':'reasoning_extraction',
                #  'explanation':"…Terms of Service … reverse engineering…"})。
                # 只搬运不解释 — 判类别归 jarvis_refusal_ledger。
                if isinstance(d, dict) and isinstance(d.get('stop_details'),
                                                      dict):
                    stop_details = dict(d.get('stop_details') or {})
                _take_usage(obj.get('usage'))

            elif et == 'message_stop':
                saw_stop = True
                break

            elif et == 'error':
                e = obj.get('error') or {}
                etype = e.get('type') if isinstance(e, dict) else '?'
                emsg = e.get('message') if isinstance(e, dict) else e
                import openai as _oa
                # evidcut-ok: 上游错误串进 exception message → 日志/降档判据, 不进 prompt
                raise _oa.APIConnectionError(
                    message='native SSE error 事件: %s: %s (已收正文 %d 字符)' % (
                        etype, str(emsg)[:160], n_text),
                    request=request)
    finally:
        try:
            resp.close()
        except Exception:
            pass

    if stop is None and not saw_stop:
        import openai as _oa
        raise _oa.APIConnectionError(
            message='native SSE incomplete (无 stop_reason / 无 message_stop, '
                    '已收正文 %d 字符)' % n_text,
            request=request)

    if gen_capped:
        _bg_log('🛑 [NativeLeg/%s] 总生成量 %d 字符 (正文 %d + 推理 %d) 触天花板 '
                '(cap=%d ≈ %d tok, = max_tokens×%.1f) → 停读关流。**常态不该响** — '
                '响了就是这一发真跑飞了 (中转原生端点不守 max_tokens, RELAY-GEN-CAP-1)'
                % (profile_name, n_gen, n_text, n_gen - n_text, gen_cap,
                   gen_cap // _CHARS_PER_TOKEN, _GEN_CAP_FACTOR))

    # 末 chunk: finish_reason + usage (与 OpenRouter 形同 — usage 可以晚于
    # finish_reason 到, 消费侧两者都收在同一个 chunk 上没问题)。
    usage = {
        'prompt_tokens': in_tok + cache_read + cache_write,
        'completion_tokens': out_tok,
        'total_tokens': in_tok + cache_read + cache_write + out_tok,
        'prompt_tokens_details': {'cached_tokens': cache_read},
        # 平铺两数 = extract_usage_fields 的第三落点 (CCUSAGE-CACHEW-1),
        # 主脑账的 cache_write/cached 全靠它们。
        'cache_read_input_tokens': cache_read,
        'cache_creation_input_tokens': cache_write,
    }
    # [APICREDIT-1-A2] 钉出口官方腿: 写缓存的 5m/1h 分项透出 (官方 usage 原样
    # 键名), 主脑账按真实时长计价 (jarvis_main_brain_usage api_* 分支)。
    # 其余腿不加键 = usage 逐字节旧形。
    if cache_split and cache_creation:
        usage['cache_creation'] = dict(cache_creation)
    yield _Chunk([_Choice(_Delta(), finish_reason=_STOP_MAP.get(stop or '', stop),
                          stop_details=stop_details)],
                 usage=usage, model=model, cid=msg_id)
