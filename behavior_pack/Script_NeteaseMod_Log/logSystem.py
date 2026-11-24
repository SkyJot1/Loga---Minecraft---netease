# -*- coding: utf-8 -*-
u"""服务器日志（服务端）—— 对标 Java 版服务器的 latest.log。

命令（仅操作员）：
    /loga              用法
    /loga all          输出全部（单次上限 MAX_OUT_LINES 条，再多只能 copy）
    /loga 50           输出最近 50 条
    /loga copy all     全部日志写进系统剪贴板
    /loga copy 50      最近 50 条写进剪贴板
    /loga setting      逐项开关记录什么（esc 退出，表单实时刷新，随存档保存）
    /loga remove_all   清空本模组在这个存档里的全部数据（之后静默 10 秒，方便直接退档）

记录分两级，默认只有标准级，详细级要自己开：

  标准级（默认开）
    START  服务器/脚本加载完成
    STOP   服务器关闭
    JOIN   进入世界（位置、维度、权限、模式）
    LEAVE  离开世界（位置、在线时长、离开原因）
    CHAT   聊天
    CMD    所有指令（原版 + 自定义，含参数），一视同仁不认命令名
    DIE    死亡（死因、击杀者、位置、维度）
    DIM    维度切换（切换前后坐标）
    TP     传送（PlayerTeleportEvent）
    PERM   权限等级变更
    MODE   游戏模式变更
    LEVEL  升级
    SLEEP  睡觉 / 起床
    FISH   钓鱼收获

  详细级（默认关，/loga setting 里逐项开）
    BREAK  破坏方块
    PLACE  放置方块
    USE    右键方块 / 打开容器
    BOX    容器物品变化（哪个箱子变了什么，不含"谁"）
    DROP   丢弃物品
    PICK   拾取物品
    EAT    进食
    HURT   受伤
    ATK    攻击实体
    ENCH   附魔台放入物品

存哪：世界 ExtraData，一行一条纯文本。四条环形流，各自覆盖，互不挤占：
    loga_i0..i7   管理（进出/死亡/维度/传送/权限/模式/升级/睡觉/钓鱼/启动停止）
    loga_c0..c5   聊天
    loga_m0..m5   指令
    loga_v0..v7   详细级（方块/容器/物品/伤害）
    loga_k        关键事件（权限变更、死亡）300 条，不参与覆盖
    loga_cfg      记录开关
    每块 200 行（约 20 KB），写满覆盖最老的一块，总占用恒定在 550 KB 上下，不用手动清。
    键前缀跟着命令名走（loga_），不和别的模组抢 log_ 这种大众名字。
    更早的 log_a0..a7（v1）和 log_i*/c*/m*/v*、log_k（0.0.5）会被读出来，但不再写入 —— 升级不丢历史；
    /loga remove_all 会连这些旧键一起清掉。

行格式（tab 分隔，导出即 .log，记事本可直接看）：
    2026-11-22 20:30:01 \t INFO \t JOIN \t 张三(10086) \t 进入世界 @ 主世界 128 64 -320

参数一律多候选容错读取：文档里叫 x，引擎里可能叫 posX，取到哪个用哪个。
取不到就少一个字段，绝不让日志自己抛异常。

这个包不认识任何别的模组：不按命令名分类，不读别人的数据，也没有任何硬编码的后门。
"""

import time

import mod.server.extraServerApi as serverApi

ServerSystem = serverApi.GetServerSystemCls()

# ---------------------------------------------------------------- 容量
#
# 四条独立环形流，各自覆盖，互不挤占：
#   聊天量大，单独一条，冲掉也冲不到管理记录；
#   指令中等，单独一条；
#   管理事件（进出/死亡/权限/维度…）量小，能留最久；
#   详细级（方块/容器/伤害）默认关，开了也只占自己那条。
#
# 每块 200 行 ≈ 20 KB；总 28 块 ≈ 550 KB，写满自动滚，占用恒定。

CHUNK_LINES = 200         # 每块行数（约 20 KB）
CHANNELS = (
    (u"i", 8),            # INFO 管理事件
    (u"c", 6),            # CHAT 聊天
    (u"m", 6),            # CMD 指令
    (u"v", 8),            # VERBOSE 详细
)
LEGACY_A_CHUNKS = 8       # v1 旧键 log_a0..a7，只读不写，升级不丢历史
# 键前缀跟着命令名走：/loga → loga_i0、loga_cfg。旧前缀只读 + 可清，不再写。
KEY_PREFIX = u"loga"
LEGACY_PREFIX = u"log"
KEY_LINES = 300           # 关键事件保留条数
FLUSH_TICKS = 300         # 15 秒落一次盘
FLUSH_LINES = 40          # 攒够这么多行也落盘
SEND_PER_TICK = 1         # 每 SEND_EVERY tick 往聊天栏推几行
# 引擎的事件队列只有 100 格、每秒大概只消化 20 条。一次 /loga all 是几百条消息，
# 4/tick（120 条/秒）会把队列打满，引擎开始 "Event queue full, dropping oldest"，
# 连玩家聊天都会被挤掉。所以放慢到约 15 条/秒。
SEND_EVERY = 2
MAX_OUT_LINES = 500       # /loga all 一次最多推多少行，再多请看 copy
COPY_CHUNK = 1200         # 剪贴板每块字符数
COPY_PER_TICK = 1         # 每 SEND_EVERY tick 发几块
MAX_COPY_CHARS = 900 * 1024   # 超过这个量不复制，免得客户端卡死
MAX_DETAIL = 200
MAX_CMD = 400             # /tellraw 那种带 JSON 的命令很长，别截太狠
# 单条聊天消息的字节预算。NotifyOneMessage 太长会被引擎砍尾巴，
# 220 字节 ≈ 73 个汉字，留足余量；超出就切成多条，续行带 ↳。
CHAT_BYTES = 220
# 玩家进世界那一刻实体还没落地，引擎给的坐标是 (x, 32768, z) 占位值。
# 所以 JOIN 延后 40 tick（约 1.3 秒）再记，跟设权限用的是同一个经验值。
JOIN_DELAY = 40
# /loga remove_all 之后静默多少 tick 不再记录（30 tick = 1 秒）
# 玩家清完存档往往直接退游戏，退出时还会触发 LEAVE / STOP / 落盘，
# 不静默的话等于刚清干净又写回去一条，所以留 10 秒空档。
MUTE_TICKS = 300
# 命令方块只记这几类：它们的结果会出现在公屏上（say/tellraw）或私聊里
# （tell/msg/w，三个是同一个命令的不同写法）。其余方块命令不记。
PUBLIC_CMDS = (u"say", u"tellraw", u"me", u"tell", u"msg", u"w",
               u"title", u"titleraw")
# 明文镜像：房主的客户端把日志写成游戏目录下的配置文件（JSON），记事本直接能看。
# 落在 storge/stream/users/<uid>/<世界id>/config/loga.txt（存档配置）。
# 带 .txt 是为了双击就能用记事本打开；万一引擎不收带点的名字，客户端会退回 loga。
MIRROR_NAME = u"loga.txt"
MIRROR_MAX = 1500         # 文件里保留最近多少行
MIRROR_TICKS = 1200       # 60 秒推一次增量
MIRROR_BUF_MAX = 4000     # 服务端缓冲上限，房主不在线时也不会无限涨
# 时间戳精确到 1/30 秒：四条流分开存，只有秒级精度的话同一秒内跨流的行会乱序
TS_LEN = 22
KEY_STORE = u"loga_k"       # 关键事件永久块
LEGACY_KEY_STORE = u"log_k"  # 0.0.5 的旧键，只清不写
CFG_KEY = u"loga_cfg"
LEGACY_CFG_KEY = u"log_cfg"

_CHUNK_TOTAL = dict(CHANNELS)
_TOTAL_CHUNKS = sum(count for _, count in CHANNELS)
_TOTAL_LINES = _TOTAL_CHUNKS * CHUNK_LINES

INVALID_UIDS = (u"", u"0", u"-1", u"4294967295", u"None")

# typ: (中文名, 颜色, 级别, 默认开关)
TYPE_META = {
    u"START":  (u"启动", u"§a", u"INFO", True),
    u"STOP":   (u"停止", u"§7", u"INFO", True),
    u"JOIN":   (u"进入", u"§a", u"INFO", True),
    u"LEAVE":  (u"离开", u"§7", u"INFO", True),
    u"CHAT":   (u"聊天", u"§7", u"INFO", True),
    u"CMD":    (u"指令", u"§f", u"INFO", True),
    u"DIE":    (u"死亡", u"§c", u"INFO", True),
    u"DIM":    (u"维度", u"§e", u"INFO", True),
    u"TP":     (u"传送", u"§e", u"INFO", True),
    u"PERM":   (u"权限", u"§c", u"INFO", True),
    u"MODE":   (u"模式", u"§e", u"INFO", True),
    u"LEVEL":  (u"升级", u"§a", u"INFO", True),
    u"SLEEP":  (u"睡觉", u"§7", u"INFO", True),
    u"FISH":   (u"钓鱼", u"§7", u"INFO", True),
    u"BREAK":  (u"破坏", u"§6", u"VERBOSE", False),
    u"PLACE":  (u"放置", u"§6", u"VERBOSE", False),
    u"USE":    (u"交互", u"§6", u"VERBOSE", False),
    u"BOX":    (u"容器", u"§6", u"VERBOSE", False),
    u"DROP":   (u"丢弃", u"§7", u"VERBOSE", False),
    u"PICK":   (u"拾取", u"§7", u"VERBOSE", False),
    u"EAT":    (u"进食", u"§7", u"VERBOSE", False),
    u"HURT":   (u"受伤", u"§c", u"VERBOSE", False),
    u"ATK":    (u"攻击", u"§c", u"VERBOSE", False),
    u"ENCH":   (u"附魔", u"§d", u"VERBOSE", False),
}

TYPE_ORDER = (u"START", u"STOP", u"JOIN", u"LEAVE", u"CHAT", u"CMD",
              u"DIE", u"DIM", u"TP", u"PERM", u"MODE", u"LEVEL", u"SLEEP",
              u"FISH", u"BREAK", u"PLACE", u"USE", u"BOX", u"DROP", u"PICK",
              u"EAT", u"HURT", u"ATK", u"ENCH")

# 中文/英文别名 → 类型码
TYPE_ALIAS = {}
for _t in TYPE_ORDER:
    TYPE_ALIAS[_t.lower()] = _t
    TYPE_ALIAS[TYPE_META[_t][0]] = _t

DIM_NAMES = {0: u"主世界", 1: u"下界", 2: u"末地"}

GAME_MODE = {0: u"生存", 1: u"创造", 2: u"冒险", 6: u"旁观"}

# ActorDamageCause → 中文。没收录的原样输出，方便对照官方枚举。
DAMAGE_CAUSE = {
    u"entity_attack": u"近战",
    u"projectile": u"弹射物",
    u"fall": u"摔落",
    u"lava": u"岩浆",
    u"fire": u"着火",
    u"fire_tick": u"火焰",
    u"soul_fire": u"灵魂火",
    u"campfire": u"营火",
    u"drowning": u"溺水",
    u"suffocation": u"窒息",
    u"starve": u"饥饿",
    u"void": u"虚空",
    u"magic": u"魔法",
    u"indirect_magic": u"药水",
    u"wither": u"凋零",
    u"anvil": u"铁砧",
    u"falling_block": u"下落方块",
    u"block_explosion": u"方块爆炸",
    u"entity_explosion": u"爆炸",
    u"lightning": u"雷击",
    u"freezing": u"冰冻",
    u"hot_floor": u"岩浆块",
    u"magma": u"岩浆块",
    u"stalactite": u"钟乳石",
    u"stalagmite": u"石笋",
    u"contact": u"接触伤害",
    u"self_destruct": u"自爆",
    u"sonic_boom": u"音波",
    u"dragon_breath": u"龙息",
    u"thorns": u"荆棘",
    u"fly_into_wall": u"撞墙",
    u"override": u"指令",
    u"piston": u"活塞",
    u"none": u"未知",
}

# 容器类型（ContainerType 枚举的常见值），只用于显示
CONTAINER_TYPE = {
    0: u"容器", 1: u"箱子", 2: u"熔炉", 3: u"酿造台", 4: u"发射器",
    5: u"投掷器", 6: u"漏斗", 7: u"炼药锅", 8: u"唱片机", 9: u"高炉",
    10: u"烟熏炉", 11: u"潜影盒", 12: u"木桶",
}


def _to_text(value):
    if value is None:
        return u""
    try:
        if isinstance(value, str):
            return value.decode("utf-8")
    except Exception:
        pass
    try:
        return u"%s" % (value,)
    except Exception:
        return u""


def _clean(text, limit):
    out = _to_text(text)
    out = out.replace(u"\t", u" ").replace(u"\r", u" ").replace(u"\n", u" ")
    if len(out) > limit:
        out = out[:limit] + u"…"
    return out


def _norm(text):
    u"""去掉开头的 / 和多余空格，小写，用来判重。"""
    out = _to_text(text).strip().lower()
    while out.startswith(u"/"):
        out = out[1:]
    return u" ".join(out.split())


def _bytes_len(text):
    u"""按 UTF-8 真实字节算，中文一字 3 字节，别按字符数糊弄。"""
    try:
        return len(text.encode("utf-8"))
    except Exception:
        return len(text)


def _split_chat(text, budget=None):
    u"""按字节预算切段，§ 颜色码不拆开。

    引擎的 NotifyOneMessage 单条太长会直接砍掉尾巴（几千字节的 tellraw JSON
    最明显），所以宁可多发几条也别让它截。续行前面加 §8↳§r，一眼看出是同一条。
    """
    if budget is None:
        budget = CHAT_BYTES
    text = _to_text(text)
    if _bytes_len(text) <= budget:
        return [text]
    mark = u"§8↳§r "
    mark_len = _bytes_len(mark)
    out = []
    cur = []
    size = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == u"§" and i + 2 <= n:
            piece = text[i:i + 2]
            i += 2
        else:
            piece = ch
            i += 1
        plen = _bytes_len(piece)
        if size + plen > budget and cur:
            out.append(u"".join(cur))
            cur = [mark]
            size = mark_len
        cur.append(piece)
        size += plen
    if cur:
        out.append(u"".join(cur))
    return out


def _pick(args, *names):
    u"""按候选名依次取值，第一个非空的赢。引擎版本间字段名不统一，靠这个兜。"""
    for name in names:
        try:
            value = args.get(name)
        except Exception:
            continue
        if value is None:
            continue
        if isinstance(value, (list, tuple, dict)) and not value:
            continue
        text = _to_text(value)
        if text and text not in (u"None", u"-1"):
            return value
    return None


def _int_or_none(value):
    try:
        return int(round(float(value)))
    except Exception:
        return None


def _item_text(item):
    u"""物品字典 → minecraft:diamond x5。拿不到就空串。"""
    if not isinstance(item, dict):
        return u""
    name = _to_text(item.get("itemName") or item.get("newItemName")
                    or item.get("name") or u"")
    if not name:
        return u""
    count = item.get("count") or item.get("newItemCount") or item.get("num")
    aux = item.get("aux") or item.get("newAuxValue") or item.get("auxValue")
    out = name
    try:
        if aux:
            out += u":%d" % int(aux)
    except Exception:
        pass
    try:
        if count and int(count) != 1:
            out += u" x%d" % int(count)
    except Exception:
        pass
    return out


class LogSystem(ServerSystem):

    def __init__(self, namespace, systemName):
        ServerSystem.__init__(self, namespace, systemName)
        self._buf = {}
        self._idx = {}
        for channel, _count in CHANNELS:
            self._buf[channel] = []
            self._idx[channel] = 0
        self._dirty = False
        self._since_flush = 0
        self._errors = 0       # SetExtraData 被引擎拒掉的次数
        self._tick = 0
        self._started = False
        self._mute_until = 0   # tick 到此为止不写任何日志（remove_all 之后的空档）
        self._names = {}
        self._uids = {}
        self._joins = {}       # player_id -> 进入时的 tick
        self._join_wait = {}   # player_id -> 到期 tick，等坐标有效再写 JOIN
        self._last = {}        # 节流键 -> tick
        self._enabled = {}     # 类型码 -> bool，覆盖默认值
        self._recent = []      # [(pid, norm, tick)] Global 收到过的命令
        self._pending = []     # 自定义命令，等 Global 确认后再决定要不要补记
        self._out = {}         # pid -> {"lines": [...], "total": n, "done": n} 慢速输出队列
        self._copy = {}        # pid -> {"text": u"...", "seq": 0} 剪贴板分块队列
        self._copy_wait = {}   # pid -> tick，等客户端剪贴板回执
        self._setting = {}     # pid -> True，处于设置模式（聊天输入被吞掉）
        self._lastcmd = {}     # pid -> (tick, 参数)，同一 tick 的同一条命令只认一次
        self._cmd_seen = {}    # pid -> (命令文本, 时间)，命令/聊天两个通道去重
        self._mirror_buf = []  # 还没推给房主客户端的行（明文镜像用）
        self._mirror_name = MIRROR_NAME   # 客户端实际写成功的文件名，可能退回无后缀
        self._mirror_on = self._mirror_supported()
        self._load_cfg()
        self._listen()

    # ---------------------------------------------------------------- 初始化

    def _listen(self):
        eng_ns = serverApi.GetEngineNamespace()
        eng_sys = serverApi.GetEngineSystemName()
        events = (
            # 生命周期
            ("LoadServerAddonScriptsAfter", self.OnLoad),
            # 每 tick 一次 —— 这个必须自己 ListenForEvent，光写个同名方法引擎不会调。
            # 少了它：输出队列不推（只看到「共 N 条」却没有内容）、剪贴板不切块、
            # 15 秒落盘不执行、设置页的指令补记也不跑。
            ("OnScriptTickServer", self.OnTick),
            # 会话
            ("ServerChatEvent", self.OnChat),
            ("GlobalCommandServerEvent", self.OnGlobal),
            # 玩家改命令方块里的指令文本（执行由 OnGlobal 兜住）
            ("CommandBlockUpdateEvent", self.OnBlockEdit),
            ("CustomCommandTriggerServerEvent", self.OnCommand),
            ("AddServerPlayerEvent", self.OnJoin),
            ("DelServerPlayerEvent", self.OnLeave),
            # 状态
            ("PlayerDieEvent", self.OnDie),
            # 维度：两个都可能存在，靠去重只记一条
            ("DimensionChangeServerEvent", self.OnDimension),
            ("ChangeDimensionServerEvent", self.OnDimension),
            ("PlayerTeleportEvent", self.OnTeleport),
            ("PlayerPermissionChangeServerEvent", self.OnPermission),
            ("GameTypeChangedServerEvent", self.OnGameType),
            ("AddLevelEvent", self.OnLevel),
            ("PlayerSleepServerEvent", self.OnSleep),
            ("PlayerStopSleepServerEvent", self.OnWakeUp),
            ("PlayerFishingAfterServerEvent", self.OnFish),
            # VERBOSE
            ("DestroyBlockEvent", self.OnBreak),
            ("ServerEntityTryPlaceBlockEvent", self.OnPlace),
            ("ServerBlockUseEvent", self.OnUse),
            ("ContainerItemChangedServerEvent", self.OnContainer),
            ("PlayerDropItemServerEvent", self.OnDrop),
            ("ServerPlayerTryTouchEvent", self.OnPick),
            ("PlayerEatFoodServerEvent", self.OnEat),
            ("PlayerHurtEvent", self.OnHurt),
            ("PlayerAttackEntityEvent", self.OnAttack),
            ("OnItemPutInEnchantingModelServerEvent", self.OnEnchant),
        )
        for name, handler in events:
            try:
                self.ListenForEvent(eng_ns, eng_sys, name, self, handler)
            except Exception:
                pass
        # 客户端剪贴板回执（客户端系统 LogClientSystem 发回来的）
        try:
            self.ListenForEvent("LogMod", "LogClientSystem", "LogCopied",
                                self, self.OnCopied)
            # 明文文件到底写成什么名字（带 .txt 还是退回无后缀），以客户端回报为准
            self.ListenForEvent("LogMod", "LogClientSystem", "LogMirrorName",
                                self, self.OnMirrorName)
        except Exception:
            pass

    def OnMirrorName(self, args):
        try:
            name = _to_text(args.get("name"))
            if name and name != self._mirror_name:
                self._mirror_name = name
        except Exception:
            pass

    # ---------------------------------------------------------------- 明文镜像
    # 存档里的正式数据还是 ExtraData（leveldb，带轮转）；这里只是把同样的行抄一份到
    # 房主客户端的配置文件里，让玩家不用工具也能用记事本打开看。
    # 官方接口：clientApi.CreateConfigClient(levelId).SetConfigData(名字, dict, False)
    # 实测落盘：<游戏目录>/storge/stream/users/<uid>/<世界id>/config/<名字>，明文 json。

    def _mirror_supported(self):
        u"""Windows 和安卓都镜像。

        安卓这个文件在 Android/data/com.netease.x19/files/storge/... 里，
        系统限制的是第三方 App 往这儿写，玩家自己用文件管理器 / 数据线是能读的。
        iOS 的沙盒没有对外的文件入口，写了也没人能看到，所以跳过。
        """
        try:
            return serverApi.GetPlatform() in (0, 2)
        except Exception:
            return True

    def _mirror_line(self, line):
        if not self._mirror_on:
            return
        try:
            self._mirror_buf.append(self._format(line))
            if len(self._mirror_buf) > MIRROR_BUF_MAX:
                self._mirror_buf = self._mirror_buf[-MIRROR_BUF_MAX:]
        except Exception:
            pass

    def _mirror_target(self):
        u"""只推给房主：世界是他开的，他一直在线，也不会有第二份文件互相覆盖。"""
        try:
            host = serverApi.GetHostPlayerId()
        except Exception:
            host = None
        if host:
            return host
        for player_id in self._joins:
            return player_id
        return None

    def _pump_mirror(self, force=False):
        if not self._mirror_buf:
            return
        if not force and self._tick % MIRROR_TICKS:
            return
        target = self._mirror_target()
        if not target:
            return
        try:
            data = self.CreateEventData()
            data["text"] = self._wire(u"\n".join(self._mirror_buf))
            data["max"] = MIRROR_MAX
            data["name"] = MIRROR_NAME
            self.NotifyToClient(target, "LogMirrorAdd", data)
        except Exception:
            return
        self._mirror_buf = []

    # ---------------------------------------------------------------- 基础

    def _level_id(self):
        return serverApi.GetLevelId()

    def _extra(self):
        return serverApi.GetEngineCompFactory().CreateExtraData(self._level_id())

    def _wire(self, text):
        u"""引擎的绑定层只认 py2 的 str（字节串）。

        unicode 传进去会被直接丢掉，Debug_Log 里留一行
        `SetLevelData Err ... type <type 'unicode'>` —— 不会抛异常，只会静默不存。
        所以键和值都必须先 encode 成 utf-8 字节串再交出去。
        py3 里 str 就是 unicode，不做转换（离线模拟跑在 py3）。
        """
        try:
            if not isinstance(text, str):
                return text.encode("utf-8")
        except Exception:
            pass
        return text

    def _get(self, key):
        try:
            return _to_text(self._extra().GetExtraData(self._wire(key)))
        except Exception:
            return u""

    def _set(self, key, value):
        try:
            ok = bool(self._extra().SetExtraData(self._wire(key),
                                                 self._wire(value)))
        except Exception:
            ok = False
        if not ok:
            self._errors += 1
        return ok

    def _now(self):
        try:
            return u"%s.%02d" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                                 self._tick % 30)
        except Exception:
            return u"--"

    def _dim(self, value):
        num = _int_or_none(value)
        if num is None:
            return u"?"
        return DIM_NAMES.get(num, u"维度%d" % num)

    def _mode(self, value):
        num = _int_or_none(value)
        if num is None:
            return u"?"
        return GAME_MODE.get(num, u"模式%d" % num)

    def _name_of(self, player_id):
        if not player_id:
            return u""
        name = self._names.get(player_id)
        if name:
            return name
        try:
            name = _to_text(serverApi.GetEngineCompFactory()
                            .CreateName(player_id).GetName())
        except Exception:
            name = u""
        if name:
            self._names[player_id] = name
        return name

    def _uid_of(self, player_id):
        if not player_id:
            return u""
        if player_id in self._uids:
            return self._uids[player_id]
        uid = u""
        try:
            uid = _to_text(serverApi.GetEngineCompFactory()
                           .CreateHttp(self._level_id()).GetPlayerUid(player_id))
        except Exception:
            uid = u""
        if uid in INVALID_UIDS:
            uid = u""
        self._uids[player_id] = uid
        return uid

    def _who(self, player_id):
        if not player_id:
            return u"-"
        name = self._name_of(player_id)
        uid = self._uid_of(player_id)
        if uid and name:
            return u"%s(%s)" % (name, uid)
        return name or uid or u"?"

    def _entity_label(self, entity_id):
        u"""击杀者 / 被攻击者：玩家给名字，其他实体给类型名。"""
        if entity_id in (None, u"", u"-1", -1):
            return u""
        text = _to_text(entity_id)
        if not text or text == u"-1":
            return u""
        name = self._name_of(text)
        if name:
            return name
        try:
            typ = _to_text(serverApi.GetEngineCompFactory()
                           .CreateEngineType(text).GetEngineTypeStr())
        except Exception:
            typ = u""
        return typ or text

    def _pos(self, player_id):
        try:
            pos = serverApi.GetEngineCompFactory().CreatePos(player_id).GetFootPos()
            x = int(round(float(pos[0])))
            y = int(round(float(pos[1])))
            z = int(round(float(pos[2])))
        except Exception:
            return None
        # 玩家刚进世界时实体还没落地，引擎给 (x, 32768, z) 这种占位坐标。
        # 记下来就是假数据，当作没读到。
        if y > 1000 or y < -1000:
            return None
        return (x, y, z)

    def _dim_of(self, player_id):
        try:
            return _int_or_none(serverApi.GetEngineCompFactory()
                                .CreateDimension(player_id).GetEntityDimensionId())
        except Exception:
            return None

    def _place_of(self, player_id):
        u"""主世界 128 64 -320"""
        pos = self._pos(player_id)
        if not pos:
            return u""
        dim = self._dim_of(player_id)
        if dim is None:
            return u"%d %d %d" % pos
        return u"%s %d %d %d" % (self._dim(dim), pos[0], pos[1], pos[2])

    def _xyz(self, args):
        # 全局命令事件给的是 blockPos=(x,y,z) 元组，不是分开的 blockX/blockY/blockZ
        pos = args.get("blockPos")
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            nums = [_int_or_none(v) for v in pos[:3]]
            if None not in nums:
                dim = _int_or_none(_pick(args, "dimensionId", "dimension", "dimId"))
                if dim is None:
                    return u"%d %d %d" % tuple(nums)
                return u"%s %d %d %d" % tuple([self._dim(dim)] + nums)
        x = _int_or_none(_pick(args, "x", "posX", "blockX", "blockPosX"))
        y = _int_or_none(_pick(args, "y", "posY", "blockY", "blockPosY"))
        z = _int_or_none(_pick(args, "z", "posZ", "blockZ", "blockPosZ"))
        if x is None or y is None or z is None:
            return u""
        dim = _int_or_none(_pick(args, "dimensionId", "dimension", "dimId"))
        if dim is None:
            return u"%d %d %d" % (x, y, z)
        return u"%s %d %d %d" % (self._dim(dim), x, y, z)

    def _is_op(self, player_id):
        try:
            comp = serverApi.GetEngineCompFactory().CreatePlayer(player_id)
            return comp.GetPlayerOperation() >= 2
        except Exception:
            return False

    def _allow(self, key, seconds):
        u"""同一把钥匙 N 秒内只放一条，防止漏斗/连续受伤把日志刷爆。"""
        now = self._tick
        last = self._last.get(key)
        gap = int(seconds * 30)
        if last is not None and now - last < gap:
            return False
        self._last[key] = now
        if len(self._last) > 400:
            self._last = {}
        return True

    # ---------------------------------------------------------------- 开关

    def _load_cfg(self):
        # 新键没有就退回去读 0.0.5 的 log_cfg，玩家调过的开关不至于白调
        raw = self._get(CFG_KEY) or self._get(LEGACY_CFG_KEY)
        if not raw:
            return
        for part in raw.split(u"\n"):
            part = part.strip()
            if u"=" not in part:
                continue
            key, _, value = part.partition(u"=")
            key = key.strip()
            if key in TYPE_META:
                self._enabled[key] = (value.strip() == u"1")

    def _save_cfg(self):
        parts = []
        for typ in TYPE_ORDER:
            if typ in self._enabled:
                parts.append(u"%s=%d" % (typ, 1 if self._enabled[typ] else 0))
        self._set(CFG_KEY, u"\n".join(parts))

    def _on(self, typ):
        meta = TYPE_META.get(typ)
        if not meta:
            return False
        if typ in self._enabled:
            return self._enabled[typ]
        return meta[3]

    def _set_type(self, typ, on):
        self._enabled[typ] = on
        self._save_cfg()

    # ---------------------------------------------------------------- 存储

    def _key_name(self, channel, index, legacy=False):
        return u"%s_%s%d" % (LEGACY_PREFIX if legacy else KEY_PREFIX,
                             channel, index)

    def _channel_of(self, typ):
        if typ == u"CHAT":
            return u"c"
        if typ == u"CMD":
            return u"m"
        meta = TYPE_META.get(typ)
        if not meta:
            return u"i"
        return u"v" if meta[2] == u"VERBOSE" else u"i"

    def _chunk_total(self, channel):
        return _CHUNK_TOTAL.get(channel, 1)

    def _muted(self):
        u"""remove_all 之后的静默期：一行都不写，连退出时的 LEAVE/STOP 也不写。"""
        return self._tick < self._mute_until

    def _write(self, typ, who, detail, limit=MAX_DETAIL):
        u"""who 已经是展示文本（张三(10086) 或 -）。"""
        if not self._on(typ):
            return
        if self._muted():
            return
        channel = self._channel_of(typ)
        meta = TYPE_META.get(typ) or (typ, u"", u"INFO", True)
        level = meta[2]
        line = u"%s\t%s\t%s\t%s\t%s" % (self._now(), level, typ,
                                        _clean(who, 40), _clean(detail, limit))
        self._mirror_line(line)
        buf = self._buf.get(channel)
        if buf is None:
            return
        buf.append(line)
        self._since_flush += 1
        self._dirty = True
        if len(buf) >= CHUNK_LINES:
            self._seal(channel)
        if typ in (u"PERM", u"DIE"):
            self._write_key(typ, who, detail, MAX_CMD)

    def _log(self, typ, player_id, detail, limit=MAX_DETAIL):
        self._write(typ, self._who(player_id), detail, limit)

    def _write_key(self, typ, who, detail, limit=MAX_CMD):
        if self._muted():
            return
        line = u"%s\t%s\t%s\t%s\t%s" % (self._now(), u"KEY", typ,
                                        _clean(who, 40), _clean(detail, limit))
        self._mirror_line(line)
        try:
            raw = self._get(KEY_STORE)
            lines = [ln for ln in raw.split(u"\n") if ln] if raw else []
            lines.append(line)
            if len(lines) > KEY_LINES:
                lines = lines[-KEY_LINES:]
            self._set(KEY_STORE, u"\n".join(lines))
        except Exception:
            pass

    def _seal(self, channel):
        buf = self._buf[channel]
        if not buf:
            return
        self._set(self._key_name(channel, self._idx[channel]), u"\n".join(buf))
        total = self._chunk_total(channel)
        self._idx[channel] = (self._idx[channel] + 1) % total
        self._buf[channel] = []
        self._dirty = True
        # 新的一块先清空，免得读到上一轮留下的旧内容
        self._set(self._key_name(channel, self._idx[channel]), u"")

    def _flush(self):
        if not self._dirty:
            return
        try:
            for channel, _count in CHANNELS:
                if self._buf[channel]:
                    self._set(self._key_name(channel, self._idx[channel]),
                              u"\n".join(self._buf[channel]))
            self._extra().SaveExtraData()
        except Exception:
            pass
        self._dirty = False
        self._since_flush = 0

    def _read_channel(self, prefix, count, legacy=False):
        out = []
        for i in range(count):
            raw = self._get(self._key_name(prefix, i, legacy))
            if raw:
                out.extend([ln for ln in raw.split(u"\n") if ln])
        return out

    def _read_all(self):
        u"""四条新流 + 旧键（0.0.5 的 log_*、v1 的 log_a*），升级不丢历史。"""
        self._flush()
        lines = []
        for channel, count in CHANNELS:
            lines += self._read_channel(channel, count)
            lines += self._read_channel(channel, count, legacy=True)
        lines += self._read_channel(u"a", LEGACY_A_CHUNKS, legacy=True)
        return lines

    def _all_desc(self):
        lines = self._read_all()
        lines.sort(key=lambda s: s[:TS_LEN])
        lines.reverse()
        return lines

    # ---------------------------------------------------------------- tick

    def OnTick(self):
        u"""引擎每 tick 回调一次（靠 _listen 里那条 OnScriptTickServer 监听）。

        不接收 args —— 这个事件本来就不带参数，别改成 OnTick(self, args)。
        """
        self._tick += 1
        if not self._started:
            self._started = True
            self._log(u"START", None, u"日志开始记录（/loga 查看用法）")
        self._flush_joins()
        self._flush_pending()
        self._pump_mirror()
        # 输出限速：引擎事件队列很小，推太快会连玩家聊天一起丢（见 SEND_EVERY）
        if self._tick % SEND_EVERY == 0:
            self._pump_output()
            self._pump_copy()
        if self._since_flush >= FLUSH_LINES or self._tick % FLUSH_TICKS == 0:
            self._flush()

    def OnLoad(self, args):
        self._started = True
        self._log(u"START", None, u"行为包加载完成")

    def Destroy(self):
        try:
            self._flush_joins(force=True)
            self._log(u"STOP", None, u"服务器关闭，日志已落盘")
            self._flush()
            self._pump_mirror(True)   # 关服前把最后这一分钟的行也推过去
        except Exception:
            pass
        try:
            ServerSystem.Destroy(self)
        except Exception:
            pass

    # ---------------------------------------------------------------- INFO 事件

    def OnChat(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            message = _to_text(_pick(args, "message", "msg", "content"))
            if not player_id or not message:
                return
            # 设置模式：把输入整个吞掉（别人看不到、也不进日志），当前面那块表单用
            if player_id in self._setting:
                try:
                    args["cancel"] = True
                except Exception:
                    pass
                self._handle_setting_input(player_id, message)
                return
            # 有的客户端把 /say、/tellraw 这类走聊天通道，命令事件根本不触发；
            # 聊天里以 / 开头的一律当命令记，文本原样保留。
            if message[:1] == u"/":
                self._log_command(player_id, message, _norm(message))
            else:
                self._log(u"CHAT", player_id, message)
        except Exception:
            pass

    def OnGlobal(self, args):
        u"""玩家、命令方块、动画执行的每一条命令。

        文档：GlobalCommandServerEvent 参数里没有 entityId，就是命令方块执行的
        （命令方块矿车、方块方块都算），只给 blockPos / dimension。
        """
        try:
            player_id = _pick(args, "entityId", "playerId", "id")
            command = _to_text(_pick(args, "command", "cmd"))
            if not command:
                return
            norm = _norm(command)
            # 自己这条命令不进日志，免得翻日志时全是 /loga 的尾巴
            if not norm or norm == u"loga" or norm.startswith(u"loga "):
                return
            if not player_id:
                self._log_block_cmd(args, command, norm)
                return
            self._recent.append((player_id, norm, self._tick))
            if len(self._recent) > 60:
                self._recent = self._recent[-40:]
            self._log_command(player_id, command, norm)
        except Exception:
            pass

    def _log_block_cmd(self, args, command, norm):
        u"""命令方块执行的命令：只留会把内容送到公屏或私聊上的那种。

        方块每分钟能跑 1200 条 fill / effect / scoreboard，那些既没人看也不是人干的，
        记了只会把日志冲掉。say / tellraw / tell / title 这些话是玩家真能看见的，
        一条就是一条，不合并、不限流 —— 已经发生的内容不该被省略。
        """
        if norm.split(u" ")[0] not in PUBLIC_CMDS:
            return
        place = self._xyz(args) or u"未知位置"
        self._write(u"CMD", u"命令方块 " + place, command, MAX_CMD)

    def OnBlockEdit(self, args):
        u"""玩家在界面里改命令方块（或矿车）的指令文本。"""
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            command = _to_text(_pick(args, "command", "cmd"))
            if not command:
                return
            place = self._xyz(args) or u"未知位置"
            kind = u"命令方块矿车" if args.get("isBlock") is False else u"命令方块"
            self._log(u"CMD", player_id, u"修改 %s %s 的指令：%s"
                      % (kind, place, command), MAX_CMD)
        except Exception:
            pass

    def OnCommand(self, args):
        u"""自定义命令：Global 没收到时补一条，收到了就不重复记。"""
        if args.get("command") != "loga":
            try:
                origin = args.get("origin") or {}
                player_id = origin.get("entityId")
                name = _to_text(args.get("command"))
                if not player_id or not name:
                    return
                values = [_to_text(it.get("value")) for it in (args.get("args") or [])
                          if isinstance(it, dict)]
                values = [v for v in values if v]
                text = u"/" + name + ((u" " + u" ".join(values)) if values else u"")
                self._pending.append((player_id, text, _norm(text), self._tick))
                if len(self._pending) > 30:
                    self._pending = self._pending[-20:]
            except Exception:
                pass
            return

        # /loga 自己
        args["return_msg_key"] = u""
        origin = args.get("origin") or {}
        player_id = origin.get("entityId")
        if not player_id:
            return
        if not self._is_op(player_id):
            self._say(player_id, u"[日志] 只有操作员可以查看日志。")
            return

        values = [_to_text(it.get("value")) for it in (args.get("args") or [])
                  if isinstance(it, dict)]
        values = [v.strip() for v in values if v and v.strip()]

        # 引擎偶尔会把一次触发派发两遍（帮助页会连着打两份）。同一条命令
        # 0.2 秒内只认一次 —— 用墙上时间而不是 tick，万一 tick 没在跳也不会把命令锁死。
        now = time.time()
        last = self._lastcmd.get(player_id)
        values = tuple(values)
        if last and last[1] == values and now - last[0] < 0.2:
            return
        self._lastcmd[player_id] = (now, values)

        if not values:
            self._help(player_id)
            return

        head = values[0].lower()
        if head in (u"all", u"全部"):
            self._output(player_id, None)
        elif head in (u"copy", u"复制"):
            self._copy_to_clipboard(player_id,
                                    values[1].lower() if len(values) > 1 else u"all")
        elif head in (u"setting", u"设置"):
            self._enter_setting(player_id)
        elif head in (u"remove_all", u"清除", u"清空"):
            self._remove_all(player_id)
        elif head.isdigit():
            self._output(player_id, int(head))
        else:
            self._help(player_id)

    def _flush_pending(self):
        if not self._pending:
            return
        keep = []
        for player_id, text, norm, tick in self._pending:
            if self._tick - tick < 2:
                keep.append((player_id, text, norm, tick))
                continue
            matched = False
            for pid, seen, when in self._recent:
                if pid == player_id and seen == norm and abs(self._tick - when) <= 3:
                    matched = True
                    break
            if not matched:
                self._log_command(player_id, text, norm)
        self._pending = keep

    def _log_command(self, player_id, text, norm):
        u"""所有指令一视同仁，不认具体命令名 —— 换成别的管理模组也不用改这里。

        同一条命令 0.5 秒内只记一次：命令事件和聊天通道可能同时送来同一条
        （/say、/tellraw 这种最容易两边都触发），不去重就记两遍。
        """
        norm = norm or _norm(text)
        now = time.time()
        last = self._cmd_seen.get(player_id)
        if last and last[0] == norm and now - last[1] < 0.5:
            return
        self._cmd_seen[player_id] = (norm, now)
        self._log(u"CMD", player_id, text, MAX_CMD)

    def OnJoin(self, args):
        try:
            player_id = _pick(args, "id", "playerId")
            if not player_id:
                return
            self._names.pop(player_id, None)
            self._uids.pop(player_id, None)
            self._name_of(player_id)
            self._uid_of(player_id)
            self._joins[player_id] = self._tick
            # 这一刻实体还没落地，坐标是 (x, 32768, z)；等 40 tick 再记。
            self._join_wait[player_id] = self._tick + JOIN_DELAY
        except Exception:
            pass

    def _flush_joins(self, only=None, force=False):
        for pid in list(self._join_wait.keys()):
            due = self._join_wait.get(pid)
            if due is None:
                continue
            if only is not None and pid != only:
                continue
            if not force and only is None and self._tick < due:
                continue
            self._join_wait.pop(pid, None)
            self._write_join(pid)

    def _write_join(self, player_id):
        try:
            bits = [u"进入世界"]
            place = self._place_of(player_id)
            if place:
                bits.append(u"@ " + place)
            try:
                op = serverApi.GetEngineCompFactory().CreatePlayer(player_id).GetPlayerOperation()
                bits.append(u"权限 %s" % op)
            except Exception:
                pass
            self._log(u"JOIN", player_id, u" · ".join(bits))
        except Exception:
            pass

    def OnLeave(self, args):
        try:
            player_id = _pick(args, "id", "playerId")
            if not player_id:
                return
            self._flush_joins(only=player_id)
            bits = [u"离开世界"]
            place = self._place_of(player_id)
            if place:
                bits.append(u"@ " + place)
            born = self._joins.pop(player_id, None)
            if born is not None:
                seconds = max(0, (self._tick - born) // 30)
                if seconds >= 60:
                    bits.append(u"在线 %dm%02ds" % (seconds // 60, seconds % 60))
                else:
                    bits.append(u"在线 %ds" % seconds)
            reason = _to_text(_pick(args, "reason", "cause", "message"))
            if reason and reason not in (u"0", u"None"):
                bits.append(u"原因 %s" % _clean(reason, 40))
            self._log(u"LEAVE", player_id, u" · ".join(bits))
            self._flush()
        except Exception:
            pass

    def OnDie(self, args):
        try:
            player_id = _pick(args, "id", "playerId", "entityId")
            if not player_id:
                return
            cause = _to_text(_pick(args, "cause", "damageCause"))
            attacker = _pick(args, "attacker", "killer", "srcId")
            killer = self._entity_label(attacker)

            bits = []
            if killer:
                bits.append(u"被 %s 击杀" % killer)
            elif cause:
                label = DAMAGE_CAUSE.get(cause, cause)
                bits.append(u"死于%s" % label)
            else:
                bits.append(u"死亡")
            place = self._place_of(player_id)
            if place:
                bits.append(u"@ " + place)
            if cause:
                label = DAMAGE_CAUSE.get(cause, cause)
                if killer:
                    bits.append(u"（%s / %s）" % (label, cause))
                else:
                    bits.append(u"（%s）" % cause)
            self._log(u"DIE", player_id, u" ".join(bits))
        except Exception:
            pass

    def OnDimension(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            if not player_id:
                return
            if not self._allow(u"dim|%s" % player_id, 3):
                return
            src = _pick(args, "fromDimensionId", "fromDimension")
            dst = _pick(args, "toDimensionId", "toDimension")
            fx = _int_or_none(_pick(args, "fromX"))
            fz = _int_or_none(_pick(args, "fromZ"))
            tx = _int_or_none(_pick(args, "toX"))
            tz = _int_or_none(_pick(args, "toZ"))
            tail = u""
            if fx is not None and fz is not None:
                tail = u" (%d, %d)" % (fx, fz)
            head = u""
            if tx is not None and tz is not None:
                head = u" (%d, %d)" % (tx, tz)
            self._log(u"DIM", player_id, u"%s%s → %s%s"
                      % (self._dim(src), tail, self._dim(dst), head))
        except Exception:
            pass

    def OnTeleport(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            if not player_id:
                return
            place = self._place_of(player_id)
            self._log(u"TP", player_id, u"传送至 %s" % place if place else u"传送")
        except Exception:
            pass

    def OnPermission(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            if not player_id:
                return
            old = _pick(args, "oldPermission", "oldPermissionLevel", "oldLevel")
            new = _pick(args, "newPermission", "newPermissionLevel", "newLevel")
            if old is None and new is None:
                return
            self._log(u"PERM", player_id, u"%s → %s" % (_to_text(old), _to_text(new)))
        except Exception:
            pass

    def OnGameType(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            if not player_id:
                return
            old = _pick(args, "oldGameType", "oldType")
            new = _pick(args, "newGameType", "newType")
            self._log(u"MODE", player_id, u"%s → %s"
                      % (self._mode(old), self._mode(new)))
        except Exception:
            pass

    def OnLevel(self, args):
        try:
            player_id = _pick(args, "id", "playerId", "entityId")
            if not player_id:
                return
            add = _int_or_none(_pick(args, "addLevel"))
            new = _int_or_none(_pick(args, "newLevel"))
            if new is not None:
                detail = u"升到 %d 级" % new
                if add and add > 1:
                    detail += u"（+%d）" % add
            else:
                detail = u"升级 +%s" % _to_text(add)
            self._log(u"LEVEL", player_id, detail)
        except Exception:
            pass

    def OnSleep(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            if player_id:
                self._log(u"SLEEP", player_id, u"睡觉")
        except Exception:
            pass

    def OnWakeUp(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            if player_id:
                self._log(u"SLEEP", player_id, u"起床")
        except Exception:
            pass

    def OnFish(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            if not player_id:
                return
            items = args.get("itemList") or []
            names = []
            for item in items:
                text = _item_text(item)
                if text:
                    names.append(text)
            self._log(u"FISH", player_id,
                      u"钓到 " + (u"、".join(names) if names else u"（空钩）"))
        except Exception:
            pass

    # ---------------------------------------------------------------- VERBOSE 事件

    def OnBreak(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            block = _to_text(_pick(args, "fullName", "blockName", "name"))
            if not block:
                return
            self._log(u"BREAK", player_id, u"%s @ %s" % (block, self._xyz(args)))
        except Exception:
            pass

    def OnPlace(self, args):
        try:
            entity_id = _pick(args, "entityId", "playerId", "id")
            block = _to_text(_pick(args, "fullName", "blockName", "name"))
            if not block:
                return
            self._log(u"PLACE", entity_id, u"%s @ %s" % (block, self._xyz(args)))
        except Exception:
            pass

    def OnUse(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            block = _to_text(_pick(args, "blockName", "fullName", "name"))
            if not block:
                return
            self._log(u"USE", player_id, u"%s @ %s" % (block, self._xyz(args)))
        except Exception:
            pass

    def OnContainer(self, args):
        try:
            pos = args.get("pos")
            pos_text = u""
            if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                dim = _int_or_none(args.get("dimensionId"))
                pos_text = u"%d %d %d" % (_int_or_none(pos[0]) or 0,
                                          _int_or_none(pos[1]) or 0,
                                          _int_or_none(pos[2]) or 0)
                if dim is not None:
                    pos_text = self._dim(dim) + u" " + pos_text
            else:
                pos_text = self._xyz(args)
            if not pos_text:
                return
            # 漏斗每秒都在动，同一个坐标 2 秒内只记一条
            if not self._allow(u"box|%s" % pos_text, 2):
                return
            ctype = _int_or_none(args.get("containerType"))
            label = CONTAINER_TYPE.get(ctype, u"容器") if ctype is not None else u"容器"
            old = _item_text(args.get("oldItemDict"))
            new = _item_text(args.get("newItemDict"))
            slot = _int_or_none(args.get("slot"))
            bits = [u"%s @ %s" % (label, pos_text)]
            if slot is not None:
                bits.append(u"槽 %d" % slot)
            if old and new:
                bits.append(u"%s → %s" % (old, new))
            elif new:
                bits.append(u"放入 %s" % new)
            elif old:
                bits.append(u"取出 %s" % old)
            self._write(u"BOX", u"-", u" · ".join(bits))
        except Exception:
            pass

    def OnDrop(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            item = _item_text(args.get("itemDict"))
            self._log(u"DROP", player_id, u"丢弃 %s" % (item or u"物品"))
        except Exception:
            pass

    def OnPick(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            item = _item_text(args.get("itemDict"))
            if not item:
                return
            self._log(u"PICK", player_id, u"拾取 %s" % item)
        except Exception:
            pass

    def OnEat(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            item = _item_text(args.get("itemDict"))
            self._log(u"EAT", player_id, u"吃掉 %s" % (item or u"食物"))
        except Exception:
            pass

    def OnHurt(self, args):
        try:
            player_id = _pick(args, "id", "playerId", "entityId")
            if not player_id:
                return
            cause = _to_text(_pick(args, "cause", "damageCause"))
            if not self._allow(u"hurt|%s|%s" % (player_id, cause), 3):
                return
            attacker = _pick(args, "attacker", "srcId", "damageSource")
            killer = self._entity_label(attacker)
            label = DAMAGE_CAUSE.get(cause, cause) or u"未知"
            detail = u"受到%s伤害" % label
            if killer:
                detail += u"，来自 %s" % killer
            if cause:
                detail += u"（%s）" % cause
            self._log(u"HURT", player_id, detail)
        except Exception:
            pass

    def OnAttack(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            victim = _pick(args, "victimId", "targetId", "id")
            target = self._entity_label(victim)
            if not target:
                return
            detail = u"攻击 %s" % target
            if _pick(args, "isCrit"):
                detail += u"（暴击）"
            self._log(u"ATK", player_id, detail)
        except Exception:
            pass

    def OnEnchant(self, args):
        try:
            player_id = _pick(args, "playerId", "entityId", "id")
            self._log(u"ENCH", player_id, u"附魔台放入物品")
        except Exception:
            pass

    # ---------------------------------------------------------------- 查询

    def _say(self, player_id, text):
        u"""发一条（或按字节预算切成几条）聊天消息。

        NotifyOneMessage 单条太长会被引擎直接截断，一条日志里带长聊天内容、
        tellraw 的 JSON、或者详细的权限字典都会撞上。所以在这里按字节切段，
        § 颜色码不拆开，续行前面加个 ↳ 标记。
        """
        try:
            comp = serverApi.GetEngineCompFactory().CreateMsg(self._level_id())
            for part in _split_chat(text):
                comp.NotifyOneMessage(player_id, part.encode("utf-8"),
                                      u"§f".encode("utf-8"))
        except Exception:
            pass

    def _parse(self, line):
        u"""兼容 v1 的 4 段与 v2 的 5 段。返回 (ts, level, typ, who, detail)。"""
        parts = line.split(u"\t")
        if len(parts) >= 5:
            return parts[0], parts[1], parts[2], parts[3], u"\t".join(parts[4:])
        while len(parts) < 4:
            parts.append(u"")
        typ = parts[1]
        level = (TYPE_META.get(typ) or (u"", u"", u"INFO", True))[2]
        return parts[0], level, typ, parts[2], u"\t".join(parts[3:])

    def _say_line(self, player_id, line):
        ts, level, typ, who, detail = self._parse(line)
        meta = TYPE_META.get(typ) or (typ, u"§f", u"INFO", True)
        mark = u"§8·" if level == u"VERBOSE" else u"§8|"
        self._say(player_id, u"§8%s§r %s %s%s§r §b%s§r %s"
                  % (ts, mark, meta[1], meta[0], who, detail))

    def _format(self, line):
        u"""存储行 → 明文文件里的一行，排版照 Java 版 latest.log。

            [2026-11-23 23:43:25] [Server thread/INFO]: SkyJot 进入世界 · 权限 2
            [2026-11-23 23:44:01] [Server thread/INFO]: <张三> 今天挖到钻石了

        时间到秒（毫秒去掉，JE 也不带）；详细级写 DEBUG；聊天用 <名字> 包起来，
        和 JE 的 <player> 一致。外壳 {"data":{...}} 是引擎 SetConfigData 自己套的，
        脚本去不掉。
        """
        ts, level, typ, who, detail = self._parse(line)
        clock = ts[:19] if len(ts) >= 19 and ts[10:11] == u" " else ts
        tag = u"DEBUG" if level == u"VERBOSE" else u"INFO"
        bits = []
        if who and who != u"-":
            bits.append(u"<%s>" % who if typ == u"CHAT" else who)
        if detail:
            bits.append(detail)
        body = u" ".join(bits) or u"-"
        body = body.replace(u"\t", u" ").replace(u"\n", u" ")
        return u"[%s] [Server thread/%s]: %s" % (clock, tag, body)

    # ---------------------------------------------------------------- 输出队列

    def _queue(self, player_id, lines, raw=False):
        u"""排队慢发 —— 一口气发几百条会被引擎吞掉，玩家也只看到最后几条。

        raw=True 表示 lines 是存储里的原始行，发之前要过 _say_line 上色；
        raw=False 表示已经是给人看的成品文本（比如设置表单）。
        """
        self._out[player_id] = {"lines": list(lines), "total": len(lines),
                                "done": 0, "raw": raw}

    def _pump_output(self):
        if not self._out:
            return
        for player_id in list(self._out.keys()):
            job = self._out.get(player_id)
            if not job:
                continue
            lines = job["lines"]
            for _ in range(SEND_PER_TICK):
                if not lines:
                    break
                line = lines.pop(0)
                if job.get("raw"):
                    self._say_line(player_id, line)
                else:
                    self._say(player_id, line)
                job["done"] += 1
            if not lines:
                if job["total"] > 1:
                    self._say(player_id, u"[日志] 输出完毕，共 %d 条。" % job["total"])
                self._out.pop(player_id, None)

    def _output(self, player_id, limit):
        u"""limit 为 None 表示全部（受 MAX_OUT_LINES 限制）。"""
        lines = self._all_desc()
        lines.reverse()
        if not lines:
            self._say_empty(player_id)
            return
        available = len(lines)
        cut = False
        if limit is None:
            count = min(available, MAX_OUT_LINES)
            cut = available > count
        else:
            count = max(1, min(limit, MAX_OUT_LINES))
        picked = lines[-count:] if count < available else lines
        head = u"[日志] 共 %d 条，输出最近 %d 条" % (available, len(picked))
        if cut:
            head += u"（单次上限 %d，全部内容用 /loga copy all）" % MAX_OUT_LINES
        self._say(player_id, head)
        self._queue(player_id, picked, raw=True)

    # ---------------------------------------------------------------- 剪贴板

    def _say_empty(self, player_id):
        u"""一条都没有时的提示。写入被拒过就多说一句，别让人以为只是没记到。"""
        self._say(player_id, u"[日志] 还没有记录。")
        if self._errors:
            self._say(player_id, u"[日志] §7注意：有 %d 次写入被引擎拒绝，"
                                  u"日志没能存进存档。§r" % self._errors)

    def _copy_to_clipboard(self, player_id, arg):
        lines = self._all_desc()
        lines.reverse()
        if not lines:
            self._say_empty(player_id)
            return
        available = len(lines)
        if arg in (u"all", u"全部", u""):
            picked = lines
        else:
            try:
                count = int(arg)
            except Exception:
                self._say(player_id, u"[日志] 用法：/loga copy all 或 /loga copy 50")
                return
            count = max(1, min(count, available))
            picked = lines[-count:]
        header = u"# 服务器日志  %s ~ %s  共 %d 条\n" % (
            picked[0][:TS_LEN], picked[-1][:TS_LEN], len(picked))
        text = header + u"\n".join(picked) + u"\n"
        size = _bytes_len(text)
        if size > MAX_COPY_CHARS:
            self._say(player_id, u"[日志] 内容 %d KB，超过 %d KB 上限。"
                      % (size // 1024, MAX_COPY_CHARS // 1024))
            self._say(player_id, u"[日志] 缩小条数再试，比如 /loga copy 300")
            return
        self._copy[player_id] = {"text": text, "seq": 0, "count": len(picked)}
        self._say(player_id, u"[日志] 正在复制 %d 条（%d KB）到剪贴板，稍等几秒…"
                  % (len(picked), max(1, size // 1024)))

    def _pump_copy(self):
        if not self._copy and not self._copy_wait:
            return
        for player_id in list(self._copy.keys()):
            job = self._copy.get(player_id)
            if not job:
                continue
            text = job["text"]
            total = (len(text) + COPY_CHUNK - 1) // COPY_CHUNK
            sent = 0
            while sent < COPY_PER_TICK and job["seq"] < total:
                seq = job["seq"]
                data = self.CreateEventData()
                data["seq"] = seq
                data["total"] = total
                # 发字节串：中文过一遍 unicode 有可能在客户端解不开
                data["text"] = self._wire(text[seq * COPY_CHUNK:(seq + 1) * COPY_CHUNK])
                try:
                    self.NotifyToClient(player_id, "LogCopyChunk", data)
                except Exception:
                    self._copy.pop(player_id, None)
                    job = None
                    self._say(player_id, u"[日志] 发送失败，客户端没有加载这个包。")
                    break
                job["seq"] += 1
                sent += 1
            if job and job["seq"] >= total:
                data = self.CreateEventData()
                data["total"] = total
                data["count"] = job["count"]
                try:
                    self.NotifyToClient(player_id, "LogCopyDone", data)
                except Exception:
                    self._say(player_id, u"[日志] 发送失败，客户端没有加载这个包。")
                self._copy.pop(player_id, None)
                # 等客户端回执，3 秒不来就说一声，别让玩家干等
                self._copy_wait[player_id] = self._tick
        # 回执超时（客户端脚本没加载 / 剪贴板被系统拦）
        for pid in list(self._copy_wait.keys()):
            if self._tick - self._copy_wait[pid] > 90:
                self._copy_wait.pop(pid, None)
                self._say(pid, u"[日志] §7客户端没回执，剪贴板没写成。"
                               u"（可能客户端脚本没加载，或系统不让写剪贴板）§r")

    def OnCopied(self, args):
        u"""客户端写完剪贴板后的回执。"""
        try:
            player_id = args.get("__id__")
            if not player_id:
                return
            self._copy_wait.pop(player_id, None)
            if args.get("ok"):
                self._say(player_id,
                          u"[日志] 已复制 %s 条（%s 字符）到剪贴板，去记事本 Ctrl+V。"
                          % (args.get("lines") or u"?", args.get("chars") or u"?"))
            else:
                self._say(player_id, u"[日志] 剪贴板写入失败，"
                                      u"可能是引擎不让写或内容太大。试试 /loga copy 300")
        except Exception:
            pass

    # ---------------------------------------------------------------- 记录设置

    def _enter_setting(self, player_id):
        self._setting[player_id] = True
        self._show_form(player_id)

    def _show_form(self, player_id):
        total = len(TYPE_ORDER)
        half = (total + 1) // 2
        lines = [u"§l[日志] §r§e记录设置§r §7输序号切换 · 输 esc 退出§r"]
        for i in range(half):
            left = i
            right = i + half
            cell = u"%2d %s %s" % (left + 1, TYPE_META[TYPE_ORDER[left]][0],
                                   u"§a开" if self._on(TYPE_ORDER[left]) else u"§8关")
            if right < total:
                cell += u"   %2d %s %s" % (right + 1, TYPE_META[TYPE_ORDER[right]][0],
                                           u"§a开" if self._on(TYPE_ORDER[right]) else u"§8关")
            lines.append(u"[日志] " + cell)
        standard = len([t for t in TYPE_ORDER if TYPE_META[t][2] == u"INFO"])
        lines.append(u"[日志] §8标准 %d 项 · 详细 %d 项 · 随存档保存§r"
                     % (standard, total - standard))
        self._queue(player_id, lines)

    def _handle_setting_input(self, player_id, message):
        text = (message or u"").strip()
        low = text.lower()
        if low in (u"esc", u"exit", u"q", u"退出"):
            self._setting.pop(player_id, None)
            self._say(player_id, u"[日志] 已退出设置，开关已保存。")
            return
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(TYPE_ORDER):
                typ = TYPE_ORDER[idx]
                self._set_type(typ, not self._on(typ))
                self._show_form(player_id)
                return
        self._show_form(player_id)

    # ---------------------------------------------------------------- 清除

    def _remove_all(self, player_id):
        # 先挂静默：这一 tick 剩下的命令回执、以及玩家接下来退出世界触发的
        # LEAVE / STOP / 落盘，全部挡掉，否则等于刚清干净又写回去一条。
        self._mute_until = self._tick + MUTE_TICKS
        removed = 0
        try:
            comp = self._extra()
            keys = []
            for channel, count in CHANNELS:
                for i in range(count):
                    keys.append(self._key_name(channel, i))
                    keys.append(self._key_name(channel, i, legacy=True))
            for i in range(LEGACY_A_CHUNKS):
                keys.append(self._key_name(u"a", i, legacy=True))
            keys.append(KEY_STORE)
            keys.append(LEGACY_KEY_STORE)
            keys.append(CFG_KEY)
            keys.append(LEGACY_CFG_KEY)
            for key in keys:
                try:
                    if comp.CleanExtraData(self._wire(key)):
                        removed += 1
                except Exception:
                    pass
            comp.SaveExtraData()
        except Exception:
            pass
        for channel, _count in CHANNELS:
            self._buf[channel] = []
            self._idx[channel] = 0
        self._dirty = False
        self._since_flush = 0
        self._enabled = {}
        # 清掉待确认的指令缓存，免得下一 tick 把清空之前的那条补写回来
        self._pending = []
        self._recent = []
        self._out.pop(player_id, None)
        self._copy.pop(player_id, None)
        self._copy_wait.pop(player_id, None)
        self._setting.pop(player_id, None)
        self._say(player_id, u"[日志] 已清除 %d 个键，这个存档里没有本模组的数据了。" % removed)
        self._say(player_id, u"[日志] §7已静默 %d 秒，现在退出存档也不会写回任何东西；"
                             u"%d 秒后若还在游戏里才会继续记录。§r"
                             % (MUTE_TICKS // 30, MUTE_TICKS // 30))

    def _help(self, player_id):
        for line in (
            u"§l[日志] §r§7===== 用法 =====",
            u"[日志] /loga §7← 这一页§r",
            u"[日志] /loga all §7← 输出全部（单次最多 %d 条）§r" % MAX_OUT_LINES,
            u"[日志] /loga 50 §7← 输出最近 50 条§r",
            u"[日志] /loga copy all §7← 复制全部到剪贴板§r",
            u"[日志] /loga copy 50 §7← 复制最近 50 条到剪贴板§r",
            u"[日志] /loga setting §7← 设置记录哪些（esc 退出）§r",
            u"[日志] /loga remove_all §7← 清空本模组在存档里的全部数据（之后静默 10 秒）§r",
        ):
            self._say(player_id, line)
        self._say(player_id, u"[日志] Windows的另存的日志文件路径："
                              u"C:\\Users\\你的用户名\\AppData\\Roaming"
                              u"\\MinecraftPC_Netease_PB\\storge\\stream\\users"
                              u"\\你的网易uid\\你的存档id\\config\\%s" % self._mirror_name)
        self._say(player_id, u"[日志] 安卓的另存的日志文件路径："
                              u"/storage/emulated/0/Android/data/com.netease.x19/files"
                              u"/storge/stream/users/你的网易uid/你的存档id/config/%s"
                              % self._mirror_name)
