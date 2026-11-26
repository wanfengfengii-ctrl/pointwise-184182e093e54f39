# 巡展双投影展务服务

流动展务组从两侧分镜及现场测点表登记画面。展务校对员核定接缝并发行固定展版，左右投影员装版、试映和走片，值班员开关场，收班员按固定报告交班。两端各有 SQLite 库，库内的当前正式画面代表实际出屏。Python 3.11 标准库即可运行。

## 运行

在项目根目录运行完整回归和演示。

```text
python3 -B check_project.py
python3 -B -m tests.support
```

`check_project.py` 由运行平台提供。也可用 `python3 -B -m unittest discover -s tests -t .` 直接发现完整套件。演示在临时目录启动三个环回监听，从 `samples/` 读取输入，办理固定发行、装版试映、开场、实显、关场及报告，对照独立预期并输出实测报告。断言不符时退出失败。

需要手工调用 API 时，在三个终端分别运行下列命令。投影端与展务端使用同一个展务数据库路径，每端另用独立本侧数据库。

```text
python3 -B -m show.http serve --db control.sqlite3 --port 8100 --left-port 8101 --right-port 8102
python3 -B -m panel.http --db left.sqlite3 --control-db control.sqlite3 --side left --port 8101
python3 -B -m panel.http --db right.sqlite3 --control-db control.sqlite3 --side right --port 8102
```

可单独使用 `python3 -B -m show.http init --db control.sqlite3` 初始化展务库。重复启动或初始化只补缺省校样和配置，不覆盖已保存的内容。入口仅监听 `127.0.0.1`，供受信任的本机展务调用者使用。展次屏态与序次按侧和展次分别记录。

## 展务请求

请求及响应使用 JSON。花括号中的路径段是标识。画面、校样、发行、展次、请求号使用字母、数字、点、下划线和连字符，并以 ASCII 字母或数字开头。左、右侧分别为 `left`、`right`。

| 方法和路径 | 输入与结果 |
| --- | --- |
| `POST /frames` | `frame, side, content, measurements`。测点用具名对象或 `[点名, 档位]` 对的数组。返回登记原件。 |
| `GET /frames/{frame}` | 领取目录原件。 |
| `PUT /drafts/{draft}` | `left, right, windows`。两侧为保留顺序的画面号数组，`windows` 为点名到 `[下界, 上界]` 的对象。保存可编辑校样。 |
| `GET /drafts/{draft}` | 读取当前校样。 |
| `POST /releases` | `release` 及可选 `draft`。省略时使用 `default_draft` 设置。成功返回固定发行，含 `playlists` 与逐点 `meters`。原号只有在新核定快照与原快照相同的情况下可以重投。 |
| `GET /releases/{release}` | 按发行号领取原快照。 |
| `GET /releases/{release}/panels/{side}` | 领取指定侧的有序片单和侧内接缝贡献。 |
| `GET /settings`、`PUT /settings/{name}` | 查看设置，或以 `value` 修改 `site_name`、`default_draft`。指定默认校样时须已有该校样。 |
| `POST /shows`、`GET /shows/{show}` | 以 `show, release` 创建准备展次，并按号查询。已有展次只能按原版重投。 |
| `POST /shows/{show}/open` | `evidence` 为两端返回的试映依据数组。展务分别回读本展实存依据，成功返回阶段 `open`。 |
| `POST /shows/{show}/cues/{side}` | `request, frame`。生成普通提示并送到对应投影端，返回该端的实显响应。 |
| `POST /shows/{show}/cues/{side}/prepare` | 同样的输入，返回尚未送达的普通提示，可另行交付给投影端。 |
| `POST /shows/{show}/close` | 空对象。固定首次正式实显的截止边界，阶段为 `closed`。 |
| `POST /shows/{show}/report`、`GET /reports/{report}` | 用空对象办理已关展次的报告。报告按 `report-{show}` 领取，办理重投返回同一份报告。 |

## 投影端请求

以下路径均发到相应一侧的监听。装版和试映通过展务阶段事务协调，准备期间可以换试映画面。开场核验使用端内当前保存的试映依据。

| 方法和路径 | 输入与结果 |
| --- | --- |
| `POST /panels/{side}/shows/{show}/install` | `release`。持久保存该侧固定片单，返回归属和片单。 |
| `POST /panels/{side}/shows/{show}/rehearse` | `release, frame`。保存试映依据，返回展次、发行、侧别、画面及尝试序次。 |
| `GET /panels/{side}/shows/{show}/rehearsal` | 读取端内保存的试映依据。 |
| `POST /panels/{side}/display` | 普通提示 `show, release, side, request, frame`。也可在失答后原样直投复领；首次出屏时重新校验阶段和本侧片单。 |
| `GET /panels/{side}/shows/{show}/receipts/{request}` | 读取原首执，未办返回 `not_found`。 |
| `GET /panels/{side}/shows/{show}/current` | 读取当前正式画面和该侧展次序次。装版后尚无正式实显时为 `null` 和 `0`。 |
| `GET /panels/{side}/shows/{show}/log` | 按实显序次返回该展该版的正式账，成功空账仍含展次、发行和侧别。 |

首次正式实显的序次按侧从 1 开始。相同请求号、发行及画面在原侧原展复投只返回原首执，参数变化返回 `receipt_conflict`。派发结果只说明提示已准备，抵达后才在展务阶段锁内裁决。关场完成后惟有既有首执可以复领，迟到的新提示不得首次实显。

## 核算和失败处理

许可闭窗的点名定义核算域。片单内每幅画面须具有同一组点名，并与本侧首幅画面的档位逐点相等。两侧同名点使用整数相加，闭区间两个端点均许可。缺键报缺测，空值、布尔值、负数、非整数及重复点名在录入时拒绝。空片单或空许可窗可以保存用于编辑，发行时拒绝。失败响应含 `error`、`message`，接缝错误还包含涉及的 `point`。坏输入为 400，未找到为 404，状态或身份冲突为 409。报告在未关展或任一侧读账失败时返回 409 `pending`，随后可重办。存储不可用或写失败返回 503。失败的首次实显不改变屏态或消耗序次。

普通样例左 L1、L2、L3 都是中缝 7、上沿 0，右 R1、R2 都是中缝 3、上沿 2。合计 10 和 2 分别落在 `[9,11]` 和 `[2,2]` 内。L3 仅用于试映，左正式播 L1、L2，右正式播 R1、R2。收班逐侧使用正式实显账对照发行原顺序，因此只把 L3 列为漏播。
