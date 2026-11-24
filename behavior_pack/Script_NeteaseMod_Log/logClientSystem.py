# -*- coding: utf-8 -*-
u"""服务器日志（客户端）—— 只干一件事：把服务端分块发来的日志拼起来，写进系统剪贴板。

服务端负责取数据、切块、按节奏推过来（一 tick 两块，避免一次塞爆）；
这边收齐了调 SetClipboardContent，然后回一句成功失败给服务端，服务端再提示玩家。

两个 py2 的坑（2026-11-22 踩过）：
  1. 引擎事件里传过来的字符串是 **字节串**（str），直接 u"%s" % value 遇到中文会抛
     UnicodeDecodeError，被 except 吞掉 → 整块丢失 → 剪贴板是空的。必须先 decode。
  2. SetClipboardContent 的参数类型是 str（py2 的字节串），传 unicode 写不进去。
"""

import mod.client.extraClientApi as clientApi

ClientSystem = clientApi.GetClientSystemCls()

# 服务端那边的 namespace / systemName，跟 modMain 里 RegisterSystem 的两个参数一致
SERVER_NS = "LogMod"
SERVER_SYSTEM = "LogSystem"


def _text(value):
    u"""把引擎递过来的值变成 unicode，中文不炸。"""
    if value is None:
        return u""
    try:
        if not isinstance(value, unicode):      # py2：str（字节串）→ unicode
            return value.decode("utf-8")
    except NameError:                            # py3 没有 unicode 这个名字
        try:
            return u"%s" % (value,)
        except Exception:
            return u""
    except Exception:
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return u""
    return value


def _wire(value):
    u"""反过来：交给引擎 api 的一律是 py2 的 str（utf-8 字节串）。"""
    try:
        if not isinstance(value, str):
            return value.encode("utf-8")
    except Exception:
        pass
    return value


class LogClientSystem(ClientSystem):

    def __init__(self, namespace, systemName):
        ClientSystem.__init__(self, namespace, systemName)
        self._parts = {}
        self._total = 0
        self._mirror = None
        self._name = u""        # 实际写成功的文件名，回报给服务端拼路径
        try:
            self.ListenForEvent(SERVER_NS, SERVER_SYSTEM, "LogCopyChunk",
                                self, self.OnChunk)
            self.ListenForEvent(SERVER_NS, SERVER_SYSTEM, "LogCopyDone",
                                self, self.OnDone)
            self.ListenForEvent(SERVER_NS, SERVER_SYSTEM, "LogMirrorAdd",
                                self, self.OnMirrorAdd)
        except Exception:
            pass

    # ------------------------------------------------------------ 明文镜像
    # 服务端每分钟把新行推过来，这边追加进配置文件。实测（2026-11-23）落盘位置：
    #   <游戏目录>/storge/stream/users/<uid>/<世界id>/config/loga.txt
    # 内容是 {"data":{"lines":["一行一条", ...]}}，明文 JSON，中文不乱码。
    # 名字带 .txt 是为了双击就能用记事本开；万一引擎不收带点的名字，退回 loga。

    def OnMirrorAdd(self, args):
        try:
            want = _text(args.get("name")) or u"loga.txt"
            limit = int(args.get("max") or 1500)
            text = _text(args.get("text"))
            if not text:
                return
            comp = clientApi.GetEngineCompFactory().CreateConfigClient(
                clientApi.GetLevelId())
            if self._mirror is None:
                self._mirror = self._load_mirror(comp, want)
            for line in text.split(u"\n"):
                if line:
                    self._mirror.append(line)
            if len(self._mirror) > limit:
                self._mirror = self._mirror[-limit:]

            name = self._save_mirror(comp, want)
            if name and name != self._name:
                self._name = name
                try:
                    data = self.CreateEventData()
                    data["name"] = name
                    self.NotifyToServer("LogMirrorName", data)
                except Exception:
                    pass
        except Exception:
            pass

    def _save_mirror(self, comp, want):
        u"""写一次；带点的名字被拒就退回无后缀。返回真正写成功的名字。

        注意：读一个不存在的配置会在磁盘上留下 {"data":{}} 的空文件，所以
        除了真要退回的那一刻，别去读另一个名字。
        """
        try:
            if bool(comp.SetConfigData(want, {"lines": self._mirror}, False)):
                return want
        except Exception:
            pass
        alt = want[:-4] if want.endswith(u".txt") else want + u".txt"
        old = []
        try:
            data = comp.GetConfigData(alt, False) or {}
            for line in (data.get("lines") or []):
                old.append(_text(line))
        except Exception:
            old = []
        if old:
            self._mirror = old + self._mirror
        try:
            if bool(comp.SetConfigData(alt, {"lines": self._mirror}, False)):
                return alt
        except Exception:
            pass
        return u""

    def _load_mirror(self, comp, name):
        u"""进游戏先把上一局写下的行读回来，不然每次启动都从头覆盖。"""
        out = []
        try:
            data = comp.GetConfigData(name, False) or {}
            for line in (data.get("lines") or []):
                out.append(_text(line))
        except Exception:
            out = []
        return out

    def OnChunk(self, args):
        try:
            seq = int(args.get("seq") or 0)
            self._parts[seq] = _text(args.get("text"))
            total = int(args.get("total") or 0)
            if total:
                self._total = total
        except Exception:
            pass

    def OnDone(self, args):
        text = u""
        count = 0
        try:
            count = int(args.get("count") or 0)
            total = int(args.get("total") or 0) or self._total
            pieces = []
            for i in range(total):
                pieces.append(self._parts.get(i) or u"")
            text = u"".join(pieces)
        except Exception:
            text = u""
        self._parts = {}
        self._total = 0

        ok = False
        if text:
            try:
                comp = clientApi.GetEngineCompFactory().CreateGame(clientApi.GetLevelId())
                ok = bool(comp.SetClipboardContent(_wire(text)))
            except Exception:
                ok = False
            # 写完再读回来确认一次：有的环境 Set 返回 True 但剪贴板还是空的
            if ok:
                try:
                    back = _text(comp.GetClipboardContent())
                    if not back:
                        ok = False
                except Exception:
                    pass

        try:
            data = self.CreateEventData()
            data["ok"] = ok
            data["chars"] = len(text)
            data["lines"] = count
            self.NotifyToServer("LogCopied", data)
        except Exception:
            pass
