-游戏内输入"/loga"查看用法

-给存档记一份日志：谁进来、谁离开、谁说了什么、谁执行了什么、谁死了、权限被谁改了

-命令：

 /loga             这一页

 /loga all           输出全部日志（单次最多 500 条）

 /loga 50           输出最近 50 条

 /loga copy all        全部日志复制到剪贴板

 /loga copy 50         最近 50 条复制到剪贴板

 /loga setting         逐项开关记录什么（输入 esc 退出）

 /loga remove_all      清空这个存档里的全部日志数据（之后静默 10 秒，方便直接退档）



-说明：

-只有操作员能用，普通玩家打 /loga 只会收到一句提示。

-数据只存在当前存档里，不联网、不上传。

-退出世界不会丢，重进还在；写满自动覆盖最老的，占用固定在 550 KB 上下。

-默认记录：进入、离开、聊天、指令、死亡、维度、传送、权限、模式、等级、睡觉、钓鱼、启停。

-详细级（破坏、放置、容器、丢弃、拾取、进食、受伤、攻击、附魔）默认关闭，在 /loga setting 里逐项打开。

-一行太长时会自动拆成几条，续行以"↳"开头，内容一字不少。

-命令方块只记 say / tellraw / tell / title 这类会被人看到的内容，fill、effect 之类不记。

-不记：成就、玩家 IP、箱子具体是谁打开的。



-在游戏外看（明文文件）：

-房主在游戏里时，日志每分钟自动写一份明文，双击就能用记事本打开。Windows：

 C:\Users\你的用户名\AppData\Roaming\MinecraftPC_Netease_PB\storge\stream\users\你的网易uid\你的存档id\config\loga.txt

-安卓（文件管理器打开；系统只拦第三方 App 写，玩家自己读不受限）：

 /storage/emulated/0/Android/data/com.netease.x19/files/storge/stream/users/你的网易uid/你的存档id/config/loga.txt

-打 /loga 会把上面两条路径（Windows 和安卓）一起打出来，不区分设备，照着打开就行（游戏不会替你写剪贴板，剪贴板只有 /loga copy 才会动）。

-"你的存档id"就是存档文件夹名；"你的网易uid"就是 users 下那个唯一的数字文件夹。

-那份文件是 JSON，里面一行一条，排版和 Java 版的 latest.log 一样：[2026-11-23 23:43:25] [Server thread/INFO]: 内容，最多留最近 1500 条，中文正常显示。外面那层 {"data":{"lines":[...]}} 是引擎写配置时自己套的壳，脚本去不掉。旧版本留下的无后缀 loga（以及只有 {"data":{}} 的空文件）可以删掉。

-iPhone 不写这份文件（沙盒没有对外的文件入口），手机上想直接看还是 /loga copy all。

-想要完整历史：/loga copy all，到记事本里 Ctrl+V，另存为 .txt 或 .log；手机上是粘到聊天框或备忘录里。

-存档里的正式数据仍然存在存档数据库（leveldb）里，那份明文是给玩家看的镜像，两边写的是同样的行。
