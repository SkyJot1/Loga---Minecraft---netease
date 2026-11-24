# -*- coding: utf-8 -*-
"""离线模拟：用假引擎跑一遍 logSystem，验证 7 条命令、队列、剪贴板、设置表单、清除。

不进游戏也能抓出逻辑错误。用法：python sim.py
"""

import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "..", "behavior_pack", "Script_NeteaseMod_Log", "logSystem.py")

STORE = {}
NAMES = {u"p1": u"张三", u"p2": u"李四", u"e1": u"僵尸"}
UIDS = {u"p1": u"10086", u"p2": u"4294967295"}
DIMS = {u"p1": 0, u"p2": 1}
OUT = []
TO_CLIENT = []      # [(pid, event, data)]


class BaseSystem(object):
    def __init__(self, namespace, systemName):
        self.namespace = namespace
        self.systemName = systemName
        self.handlers = {}

    def ListenForEvent(self, ns, sysname, event, obj, func):
        self.handlers[(ns, sysname, event)] = func

    def NotifyToClient(self, pid, event, data):
        TO_CLIENT.append((pid, event, dict(data)))

    def CreateEventData(self):
        return {}

    def Destroy(self):
        pass


class Extra(object):
    def GetExtraData(self, key):
        return STORE.get(key, u"")

    def SetExtraData(self, key, value):
        STORE[key] = value
        return True

    def CleanExtraData(self, key):
        return STORE.pop(key, None) is not None

    def SaveExtraData(self):
        return True


class Msg(object):
    def NotifyOneMessage(self, pid, text, color):
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        OUT.append(text)


class Name(object):
    def __init__(self, pid):
        self.pid = pid

    def GetName(self):
        return NAMES.get(self.pid, u"")


class Http(object):
    def GetPlayerUid(self, pid):
        return UIDS.get(pid, u"4294967295")


class Player(object):
    def GetPlayerOperation(self):
        return 2


class Pos(object):
    def __init__(self, pid):
        self.pid = pid

    def GetFootPos(self):
        return (1.2, 64.0, 3.6)


class Dimension(object):
    def __init__(self, pid):
        self.pid = pid

    def GetEntityDimensionId(self):
        return DIMS.get(self.pid, 0)


class EngineType(object):
    def __init__(self, eid):
        self.eid = eid

    def GetEngineTypeStr(self):
        return u"minecraft:zombie" if self.eid == u"e1" else u""


class Factory(object):
    def CreateExtraData(self, level):
        return Extra()

    def CreateMsg(self, level):
        return Msg()

    def CreateName(self, pid):
        return Name(pid)

    def CreateHttp(self, level):
        return Http()

    def CreatePlayer(self, pid):
        return Player()

    def CreatePos(self, pid):
        return Pos(pid)

    def CreateDimension(self, pid):
        return Dimension(pid)

    def CreateEngineType(self, eid):
        return EngineType(eid)


def install_stub():
    mod = types.ModuleType("mod")
    mod.__path__ = []
    server = types.ModuleType("mod.server")
    server.__path__ = []
    api = types.ModuleType("mod.server.extraServerApi")
    api.GetServerSystemCls = lambda: BaseSystem
    api.GetEngineNamespace = lambda: "engine"
    api.GetEngineSystemName = lambda: "server"
    api.GetLevelId = lambda: "level"
    api.GetEngineCompFactory = lambda: Factory()
    api.GetPlayerList = lambda: list(NAMES.keys())
    sys.modules["mod"] = mod
    sys.modules["mod.server"] = server
    sys.modules["mod.server.extraServerApi"] = api


def load():
    install_stub()
    spec = importlib.util.spec_from_file_location("logSystem", TARGET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def drain(system, ticks=400):
    for _ in range(ticks):
        system.OnTick()


def show(title):
    print("\n--- %s ---" % title)
    for line in OUT:
        print(line)
    del OUT[:]


def cmd(system, pid, *values, **kw):
    args = [{"name": "arg%d" % i, "value": v} for i, v in enumerate(values)]
    system.OnCommand({"command": "loga",
                      "origin": {"entityId": pid},
                      "args": args,
                      "variant": kw.get("variant", 0)})


class FakeClipboard(object):
    """模拟客户端：收块 → 拼接 → 写剪贴板。"""

    def __init__(self):
        self.parts = {}
        self.text = None

    def feed(self):
        got_done = False
        for pid, event, data in TO_CLIENT:
            if event == "LogCopyChunk":
                text = data["text"]
                # 服务端现在发的是 utf-8 字节串（py2 引擎只认 str）
                if isinstance(text, bytes):
                    text = text.decode("utf-8")
                self.parts[data["seq"]] = text
            elif event == "LogCopyDone":
                total = data["total"]
                self.text = u"".join([self.parts.get(i, u"") for i in range(total)])
                self.count = data.get("count")
                self.parts = {}
                got_done = True
        del TO_CLIENT[:]
        return got_done


def main():
    module = load()
    system = module.LogSystem("LogMod", "LogSystem")

    print("注册的事件 / 自定义事件（%d 个）：" % len(system.handlers))
    for ns, sysname, event in sorted(system.handlers.keys()):
        print("  %s / %s / %s" % (ns, sysname, event))

    # ---------------------------------------------- 造数据（每条之间推 tick，模拟真实时间戳）
    def step(*_a, **_k):
        system.OnTick()

    step()
    system.OnJoin({"id": "p1"}); step()
    system.OnJoin({"id": "p2"}); step()
    # JOIN 会延后 40 tick 才写（进世界那一刻引擎给的坐标是 (x, 32768, z)），把时间推过去
    for _ in range(41):
        step()
    system.OnChat({"playerId": "p1", "message": u"今天挖到钻石了"}); step()
    system.OnChat({"playerId": "p2", "message": u"带\ttab 和换行\n的脏数据"}); step()
    system.OnGlobal({"entityId": "p1", "command": u"/sethome 矿洞"}); step()
    system.OnGlobal({"entityId": "p2", "command": u"/tp 0 64 0"}); step()
    system.OnDie({"id": "p1", "cause": "fall"}); step()
    system.OnDie({"id": "p2", "cause": "entity_attack", "attacker": "e1"}); step()
    system.OnDimension({"entityId": "p1", "fromDimensionId": 0, "toDimensionId": 1,
                        "fromX": 10, "fromZ": -5, "toX": 2, "toZ": 3}); step()
    system.OnPermission({"playerId": "p2", "oldPermission": 1, "newPermission": 2}); step()
    # 自定义命令：Global 已记过时不重复，Global 没记时要补
    system.OnCommand({"command": "setwarp", "origin": {"entityId": "p1"},
                      "args": [{"name": "n", "value": u"主城"}], "variant": 0})
    for _ in range(3):
        system.OnTick()

    # 详细级默认关，应先被丢掉
    before = len(system._read_all())
    system.OnBreak({"playerId": "p1", "fullName": "minecraft:stone",
                    "x": 1, "y": 64, "z": 3, "dimensionId": 0})
    after = len(system._read_all())
    print("\n详细事件默认被丢弃：%s（%d → %d）"
          % ("是" if after == before else "否，漏了", before, after))

    # ---------------------------------------------- 1. /loga 用法
    cmd(system, "p1")
    show(u"/loga（用法）")

    # ---------------------------------------------- 2. /loga 5（args1，纯数字）
    cmd(system, "p1", u"5", variant=1)
    drain(system, 10)
    show(u"/loga 5")

    # ---------------------------------------------- 3. /loga all
    cmd(system, "p1", u"all")
    drain(system, 30)
    head = OUT[:4]
    tail = OUT[-2:]
    total_lines = len(OUT)
    del OUT[:]
    print("\n--- /loga all ---")
    print("共发出 %d 行，头 4 行：" % total_lines)
    for line in head:
        print("  " + line)
    print("  …")
    for line in tail:
        print("  " + line)

    # ---------------------------------------------- 4. /loga copy all
    clip = FakeClipboard()
    cmd(system, "p1", u"copy", u"all")
    drain(system, 20)
    done = clip.feed()
    print("\n--- /loga copy all ---")
    print("收到完成信号：%s" % done)
    print("剪贴板字符数：%d" % len(clip.text or u""))
    for line in (clip.text or u"").split(u"\n")[:3]:
        print("  " + line)
    system.OnCopied({"__id__": "p1", "ok": True,
                     "chars": len(clip.text or u""), "lines": clip.count})
    show(u"复制回执")

    # ---------------------------------------------- 5. /loga copy 3
    clip = FakeClipboard()
    cmd(system, "p1", u"copy", u"3", variant=2)
    drain(system, 20)
    clip.feed()
    print("\n/loga copy 3 → 剪贴板行数：%d"
          % len((clip.text or u"").strip().split(u"\n")))

    # ---------------------------------------------- 6. /loga setting
    cmd(system, "p1", u"setting")
    drain(system, 10)
    show(u"/loga setting（表单）")

    system.OnChat({"playerId": "p1", "message": u"15"})
    drain(system, 10)
    print("\n--- 输入 15 之后 ---")
    print("BREAK 现在：%s" % ("开" if system._on(u"BREAK") else "关"))
    print("输入是否进日志：%s"
          % ("没进（已吞）" if not any(u"\tCHAT\t" in ln and u"\t15" in ln
                                    for ln in system._read_all()) else "进了"))
    for line in OUT:
        if u"破坏" in line:
            print("  表单对应行：" + line)
    del OUT[:]

    system.OnChat({"playerId": "p1", "message": u"15"})
    drain(system, 10)
    print("再输 15 之后 BREAK：%s" % ("开" if system._on(u"BREAK") else "关"))

    system.OnChat({"playerId": "p1", "message": u"esc"})
    print("esc 退出设置：%s" % ("是" if "p1" not in system._setting else "否"))
    system.OnChat({"playerId": "p1", "message": u"正常聊天"})
    print("退出后聊天恢复记录：%s"
          % ("是" if any(u"正常聊天" in ln for ln in system._read_all()) else "否"))
    del OUT[:]

    # ---------------------------------------------- 6.5 命令方块
    print("\n--- 命令方块 ---")
    # 文档示例：没有 entityId 就是命令方块执行的
    system.OnGlobal({"cancel": False, "command": u"/say I am a command_block",
                     "dimension": 0, "blockPos": (226, 64, 579)})
    for _ in range(10):
        system.OnTick()
        system.OnGlobal({"cancel": False, "command": u"/say I am a command_block",
                         "dimension": 0, "blockPos": (226, 64, 579)})
    system.OnGlobal({"command": u"/fill ~ ~ ~ ~1 ~1 ~1 stone", "dimension": 1,
                     "blockPos": (-8, 70, 12)})
    system.OnGlobal({"command": u"/w SkyJot 你来一下", "dimension": 0,
                     "blockPos": (1, 2, 3)})
    system.OnBlockEdit({"playerId": "p1", "command": u"/tp @p 0 100 0",
                        "isBlock": True, "blockX": 10, "blockY": 20,
                        "blockZ": 30, "dimension": 0})
    cb_lines = [ln for ln in system._read_all() if u"命令方块" in ln]
    for ln in cb_lines:
        print("  " + ln)
    print("  同一条 /say 执行 11 次，记下 %d 条（应 11，不合并）：%s"
          % (len([ln for ln in cb_lines if u"command_block" in ln]),
             "是" if len([ln for ln in cb_lines if u"command_block" in ln]) == 11 else "否"))
    print("  /w 私信也记：%s"
          % ("是" if any(u"/w SkyJot" in ln for ln in cb_lines) else "否"))
    print("  /fill 这类不上公屏的不记：%s"
          % ("是" if not any(u"fill" in ln for ln in cb_lines) else "否"))

    # ---------------------------------------------- 7. /loga remove_all
    # 顺便塞两个 0.0.5 的旧键，验证兼容读取 + 一并清理
    STORE["log_i0"] = u"2026-11-22 20:00:00.01\tINFO\tCHAT\t-\t改名之前的旧日志"
    STORE["log_cfg"] = u"BREAK=1"
    legacy_seen = any(u"改名之前的旧日志" in ln for ln in system._read_all())
    print("\n旧键 log_i0 还能读到：%s" % ("是" if legacy_seen else "否"))
    before_keys = sorted([k for k in STORE
                          if k.startswith("loga_") or k.startswith("log_")])
    cmd(system, "p1", u"remove_all")
    after_keys = sorted([k for k in STORE
                         if k.startswith("loga_") or k.startswith("log_")])
    print("\n--- /loga remove_all ---")
    print("清除前键数：%d → 清除后：%d" % (len(before_keys), len(after_keys)))
    print("残留：%s" % (", ".join(after_keys) or "无"))
    print("开关已重置：%s" % ("是" if not system._enabled else "否"))
    show(u"清除回执")

    # ---------------------------------------------- 7b. 清完之后 10 秒静默
    # 复现真实场景：清完存档 → 聊天 / 打指令 / 退出世界 → Destroy
    quiet = module.MUTE_TICKS
    system.OnChat({"playerId": "p1", "message": u"清完之后还有人说话"})
    system.OnGlobal({"entityId": "p1", "command": u"/kill"})
    system.OnJoin({"id": "p2"})
    system.OnDie({"id": "p2", "cause": "fall"})
    system.OnLeave({"id": "p1"})
    system.Destroy()
    drain(system, quiet - 12)
    muted_keys = sorted([k for k in STORE if k.startswith("log_")])
    print("\n--- 清完之后的静默期（%d tick）---" % quiet)
    print("期间产生的新键：%s" % (", ".join(muted_keys) or "无"))
    print("缓冲有没有偷偷攒行：%s"
          % ("没有" if not any(system._buf[c] for c, _n in module.CHANNELS) else "攒了，会漏"))
    # 静默期过了要能自己恢复
    drain(system, 20)
    system.OnChat({"playerId": "p1", "message": u"静默期过了这条应该记下"})
    resumed = system._read_all()
    print("静默结束后恢复记录：%s（%d 条）"
          % ("是" if any(u"静默期过了" in ln for ln in resumed) else "否", len(resumed)))

    # ---------------------------------------------- 8. 输出上限
    STORE.clear()
    system2 = module.LogSystem("LogMod", "LogSystem")
    for i in range(module.MAX_OUT_LINES + 40):
        system2.OnChat({"playerId": "p1", "message": u"刷屏 %d" % i})
    OUT[:] = []
    system2._output("p1", None)
    job = system2._out.get("p1")
    print("\n--- 上限保护 ---")
    print("/loga all 队列：%d 行（上限 %d + 1 行标题）"
          % (job["total"], module.MAX_OUT_LINES))
    print("提示行：%s" % job["lines"][0])

    # ---------------------------------------------- 9. 超量拒绝复制
    OUT[:] = []
    system2._copy_to_clipboard("p1", u"all")
    if system2._copy.get("p1"):
        print("\n复制队列已建立：%d 字符" % len(system2._copy["p1"]["text"]))
    else:
        print("\n超量复制被拒绝：")
        for line in OUT:
            print("  " + line)


if __name__ == "__main__":
    main()
