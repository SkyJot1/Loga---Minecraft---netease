# -*- coding: utf-8 -*-

from common.mod import Mod
import mod.server.extraServerApi as serverApi
import mod.client.extraClientApi as clientApi


@Mod.Binding(name="NeteaseMod_Log", version="1.0.0")
class NeteaseMod_Log(object):

    def __init__(self):
        pass

    @Mod.InitServer()
    def ServerInit(self):
        serverApi.RegisterSystem(
            "LogMod",
            "LogSystem",
            "Script_NeteaseMod_Log.logSystem.LogSystem"
        )

    @Mod.DestroyServer()
    def ServerDestroy(self):
        pass

    @Mod.InitClient()
    def ClientInit(self):
        clientApi.RegisterSystem(
            "LogMod",
            "LogClientSystem",
            "Script_NeteaseMod_Log.logClientSystem.LogClientSystem"
        )

    @Mod.DestroyClient()
    def ClientDestroy(self):
        pass
